#!/bin/bash

# Script to create systemd .link files for persistent network interface names and aliases.
# Reads MAC addresses, names and altnames from the host config, validates, and generates one
# /etc/systemd/network/10-pancakebatter-<name>.link per NIC (Name= and AlternativeName=,
# matched on the permanent MAC so VLANs/bridges sharing a MAC are not renamed).
# NICs with no altname in the config are left alone. Requires a reboot to take effect.
#
#   sudo ./network_alias_assignment.sh            install the files, then offer a reboot
#   ./network_alias_assignment.sh --dry-run       print the files; changes nothing, needs no root

# Color codes
RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
BLUE="\033[0;34m"
NC="\033[0m"  # No Color

# --- Configuration ---
# Get hostname (use -s for short name, adjust if your naming needs FQDN)
source "$(dirname "${BASH_SOURCE[0]}")/lib/host_config.sh"
HOSTNAME="$HOST_NAME"
CONFIG_FILE="$HOST_CONFIG_FILE"
LINK_DIR="/etc/systemd/network"
LINK_PREFIX="10-pancakebatter-"
LEGACY_RULES_FILE="/etc/udev/rules.d/10-network-aliases.rules"  # older versions wrote NAME= udev rules here
STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT
# User who invoked sudo, for running yq safely
INVOKING_USER="$SUDO_USER"

# --dry-run: print the udev rules that would be written; touch nothing, no root, no reboot prompt.
DRY_RUN=false
case "${1:-}" in
    --dry-run) DRY_RUN=true ;;
    "") ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 1 ;;
esac
if $DRY_RUN; then
    INVOKING_USER="${SUDO_USER:-$(id -un)}"
    echo "DRY RUN: nothing will be installed; the .link files are printed at the end."
fi

# --- Helper Functions ---

# Function to check for required commands
check_dependencies() {
    echo -e "${YELLOW}Checking dependencies...${NC}"
    local missing_deps=0
    local deps=("yq") # Only yq is needed now
    for cmd in "${deps[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            echo -e "${RED}Error: Required command '$cmd' not found.${NC}"
            missing_deps=1
        fi
    done
    if [[ "$missing_deps" -eq 0 ]] && ! printf '{}\n' | run_as_invoking_user yq e '.test' - >/dev/null 2>&1; then
        echo -e "${RED}Error: Installed yq does not support 'yq e' syntax. Run install_yq.sh.${NC}"
        missing_deps=1
    fi
    if [[ "$missing_deps" -eq 1 ]]; then
        echo -e "${RED}Please install missing dependencies and try again.${NC}"
        exit 1
    fi
    echo -e "${GREEN}All dependencies found.${NC}"
}

# Run a command as the invoking user (directly, when not root: dry-run as a normal user)
run_as_invoking_user() {
    if [[ "$EUID" -eq 0 ]]; then sudo -u "$INVOKING_USER" "$@"; else "$@"; fi
}

# Function to safely parse YAML using yq as the invoking user
safe_yq_get() {
    local query=$1
    local file=$2
    run_as_invoking_user yq e "$query" "$file" 2>/dev/null
    return $? # Return yq's exit code
}

validate_mac_address() {
    local mac=$1
    local valid_mac_regex="^([0-9a-fA-F]{2}[:-]){5}([0-9a-fA-F]{2})$"
    if [[ -z "$mac" || "$mac" == "null" || ! $mac =~ $valid_mac_regex ]]; then
        echo -e "${RED}  Invalid or missing MAC address format: '$mac'${NC}"
        return 1
    fi
    return 0
}

validate_interface_name() {
    local name=$1
    local context=$2 # e.g., "primary name" or "altname"
    # Interface name must be alphanumeric, underscore, hyphen and no longer than 15 characters (IFNAMSIZ)
    local valid_name_regex="^[a-zA-Z0-9_-]{1,15}$"
    if [[ -z "$name" || "$name" == "null" || ! $name =~ $valid_name_regex ]]; then
        echo -e "${RED}  Invalid or missing $context format: '$name' (must be 1-15 alphanumeric/underscore/hyphen chars)${NC}"
        return 1
    fi
    return 0
}

# --- Main Script ---

# Check if running as root
if [[ "$EUID" -ne 0 ]] && ! $DRY_RUN; then
    echo -e "${RED}Please run as root using sudo${NC}"
    exit 1
fi

# Ensure SUDO_USER is set
if [[ -z "$INVOKING_USER" ]]; then
     echo -e "${RED}Error: Could not determine the original user. Please run using sudo.${NC}"
     exit 1
fi

# Check dependencies first
check_dependencies

# Check if config file exists
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo -e "${RED}Configuration file '$CONFIG_FILE' not found for hostname '$HOSTNAME'.${NC}"
    exit 1
fi
echo -e "${GREEN}Using configuration file: $CONFIG_FILE${NC}"

echo -e "${YELLOW}Staging .link files in $STAGE_DIR (installed to $LINK_DIR only on a real run)...${NC}"

# Parse the number of NICs from the config file
nics_length=$(safe_yq_get '.nics | length' "$CONFIG_FILE")
if ! [[ "$nics_length" =~ ^[0-9]+$ ]]; then
     echo -e "${RED}Error: Could not parse number of NICs from $CONFIG_FILE${NC}"
     exit 1
fi

echo -e "${YELLOW}Generating .link files...${NC}"
local_errors=0

# Iterate through the nics array
for ((i=0; i<$nics_length; i++)); do
    nic_index_errors=0
    echo -e "${BLUE}Processing NIC definition index: $i${NC}"

    # Get the primary interface name (the key under nics[i])
    nic_name=$(safe_yq_get ".nics[$i] | keys | .[0]" "$CONFIG_FILE")
    if ! validate_interface_name "$nic_name" "primary name"; then
        ((nic_index_errors++))
    fi

    # Extract MAC and altname using the primary name
    mac_address=$(safe_yq_get ".nics[$i].$nic_name.mac_address" "$CONFIG_FILE" | tr '[:upper:]' '[:lower:]') # Ensure lowercase for consistency
    if ! validate_mac_address "$mac_address"; then
        ((nic_index_errors++))
    fi

    altname=$(safe_yq_get ".nics[$i].$nic_name.altname" "$CONFIG_FILE")
    if [[ "$altname" == "null" || -z "$altname" ]] && [[ "$nic_index_errors" -eq 0 ]]; then
        echo -e "  ${YELLOW}$nic_name has no altname: leaving this NIC's name unchanged (no rule written).${NC}"
        continue
    fi
    if ! validate_interface_name "$altname" "altname"; then
        ((nic_index_errors++))
    fi

    # Skip writing rules for this NIC if any validation failed
    if [[ "$nic_index_errors" -gt 0 ]]; then
         echo -e "${RED}  Skipping .link generation for NIC index $i due to validation errors.${NC}"
         ((local_errors++))
         continue
    fi

    # If validations passed, stage the .link file
    echo -e "  Adding .link for MAC $mac_address -> Name=$nic_name, AlternativeName=$altname"
    cat > "$STAGE_DIR/${LINK_PREFIX}${nic_name}.link" << LINKEOF
# Generated by network_alias_assignment.sh from $CONFIG_FILE. Do not edit; re-run the script.
[Match]
PermanentMACAddress=$mac_address

[Link]
Name=$nic_name
AlternativeName=$altname
AlternativeNamesPolicy=database onboard slot path
LINKEOF

done # End loop through NICs


# Final status message
shopt -s nullglob
staged=("$STAGE_DIR"/*.link)
if [[ "$local_errors" -gt 0 ]]; then
    echo -e "${YELLOW}Finished generating files, but $local_errors NIC definition(s) had errors and were skipped.${NC}"
else
    echo -e "${GREEN}Successfully generated all .link files (${#staged[@]}).${NC}"
fi

if $DRY_RUN; then
    for f in "${staged[@]}"; do
        echo -e "\n${BLUE}--- would install $LINK_DIR/$(basename "$f") ---${NC}"
        cat "$f"
    done
    [[ -f "$LEGACY_RULES_FILE" ]] && echo -e "\n${YELLOW}Would disable legacy $LEGACY_RULES_FILE (renamed to *.disabled-<date>).${NC}"
    exit $(( local_errors > 0 ? 1 : 0 ))
fi

if [[ ${#staged[@]} -eq 0 ]]; then
    echo -e "${YELLOW}Nothing to install; existing files left untouched.${NC}"
    exit $(( local_errors > 0 ? 1 : 0 ))
fi

# Install: the config is the source of truth, so replace any earlier pancakebatter .link files.
mkdir -p "$LINK_DIR"
rm -f "$LINK_DIR/${LINK_PREFIX}"*.link
for f in "${staged[@]}"; do
    install -o root -g root -m 0644 "$f" "$LINK_DIR/"
done
echo -e "${GREEN}Installed ${#staged[@]} .link file(s) to $LINK_DIR${NC}"
if [[ -f "$LEGACY_RULES_FILE" ]]; then
    legacy_backup="${LEGACY_RULES_FILE}.disabled-$(date +%Y%m%d-%H%M%S)"
    mv "$LEGACY_RULES_FILE" "$legacy_backup"
    echo -e "${YELLOW}Disabled legacy udev rules (they would conflict): $legacy_backup${NC}"
fi
udevadm control --reload 2>/dev/null || true

# Prompt for reboot
echo -e "\n${YELLOW}Names and aliases are applied by udev when each interface appears, so a reboot is the cleanest way to apply them.${NC}"
echo -e "${YELLOW}If these links are also needed in early boot, run 'sudo update-initramfs -u' before rebooting.${NC}"
echo -e "${YELLOW}Would you like to:${NC}"
echo "1. Save files and reboot now"
echo "2. Save files only (manual reboot required)"
read -p "Enter choice (1 or 2): " choice

case $choice in
    1)
        echo -e "${GREEN}Rebooting system...${NC}"
        sync # Ensure all changes are written to disk
        systemctl reboot
        ;;
    2|*) # Default to saving only if choice is not 1
        echo -e "${GREEN}Files saved to $LINK_DIR. Please reboot your system when convenient.${NC}"
        echo -e "${YELLOW}After reboot, run create_nm_connections.sh and then configure_interfaces.sh for the newly named interfaces.${NC}"
        ;;
esac

exit 0
