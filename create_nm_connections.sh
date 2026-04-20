#!/bin/bash

# Script to create NetworkManager connection profiles for interfaces defined in the config file.
# This script is idempotent: it checks if a connection already exists before attempting to create one.
# Should be run AFTER rebooting with udev rules applied, and BEFORE configuring IP settings.

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# --- Configuration ---
# Get hostname (use -s for short name, adjust if your naming needs FQDN)
HOSTNAME=$(hostname -s)
CONFIG_FILE="./${HOSTNAME}_config.yml"
# User who invoked sudo, for running yq safely
INVOKING_USER="$SUDO_USER"

# --- Helper Functions ---
# Function to check for required commands
check_dependencies() {
    echo -e "${YELLOW}Checking dependencies...${NC}"
    local missing_deps=0
    local deps=("yq" "nmcli" "ip") # ip needed to check if interface exists
    for cmd in "${deps[@]}"; do
        if ! command -v "$cmd" &> /dev/null; then
            echo -e "${RED}Error: Required command '$cmd' not found.${NC}"
            missing_deps=1
        fi
    done
    if [[ "$missing_deps" -eq 0 ]] && ! printf '{}\n' | sudo -u "$INVOKING_USER" yq e '.test' - >/dev/null 2>&1; then
        echo -e "${RED}Error: Installed yq does not support 'yq e' syntax. Run install_yq.sh.${NC}"
        missing_deps=1
    fi
    if [[ "$missing_deps" -eq 1 ]]; then
        echo -e "${RED}Please install missing dependencies and try again.${NC}"
        exit 1
    fi
    echo -e "${GREEN}All dependencies found.${NC}"
}

# Function to safely parse YAML using yq as the invoking user
safe_yq_get() {
    # Function variables are local by default unless declared otherwise elsewhere
    local query=$1
    local file=$2
    # Run yq as the original user
    sudo -u "$INVOKING_USER" yq e "$query" "$file" 2>/dev/null
    local exit_status=$?
    # Check yq exit status. 0=success, 4=null/empty, other=error
    if [[ $exit_status -ne 0 && $exit_status -ne 4 ]]; then
         # Output special string on actual error
         echo "YAML_PARSE_ERROR"
         return 1 # Signal error with return code
    fi
    # Return the actual output (which might be empty/null if exit_status was 4)
    # Return yq's exit code (0 or 4) on success/null
    return $exit_status
}

validate_interface_name() {
    local name=$1
    local context=$2 # e.g., "primary name" or "altname"
    local valid_name_regex="^[a-zA-Z0-9_-]{1,15}$"
    if [[ -z "$name" || "$name" == "null" || ! $name =~ $valid_name_regex ]]; then
        # Print error to stderr
        echo -e "${RED}  Invalid or missing $context format: '$name' (must be 1-15 alphanumeric/underscore/hyphen chars)${NC}" >&2
        return 1
    fi
    return 0
}

# --- Main Script ---

# Check if running as root
if [[ "$EUID" -ne 0 ]]; then
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

# Parse the number of NICs from the config file
nics_length_raw=$(safe_yq_get '.nics | length' "$CONFIG_FILE")
# Check exit code of safe_yq_get OR the output format
if [[ $? -ne 0 || ! "$nics_length_raw" =~ ^[0-9]+$ ]]; then
     echo -e "${RED}Error: Could not parse number of NICs from $CONFIG_FILE${NC}"
     exit 1
fi
nics_length=$nics_length_raw

echo -e "${YELLOW}Ensuring NetworkManager connections exist for configured interfaces...${NC}"
local_errors=0

# Loop through NICs defined in the config file
for ((i=0; i<$nics_length; i++)); do
    # Get the primary interface name
    nic_name=$(safe_yq_get ".nics[$i] | keys | .[0]" "$CONFIG_FILE")
    yq_exit_status=$? # Capture exit status of safe_yq_get

    # --- Check for parsing or validation errors ---
    is_parse_error=0
    # Check if safe_yq_get returned error code OR if output is the error string OR empty
    if [[ "$yq_exit_status" -ne 0 && "$yq_exit_status" -ne 4 ]] || [[ "$nic_name" == "YAML_PARSE_ERROR" ]] || [[ -z "$nic_name" ]]; then
        is_parse_error=1
    fi

    # Check if the name format is valid using the function's exit code
    validate_interface_name "$nic_name" "primary name"
    is_validation_error=$? # 0 if valid, non-zero if invalid

    # Combine the checks
    if [[ "$is_parse_error" -eq 1 || "$is_validation_error" -ne 0 ]]; then
        if [[ "$is_parse_error" -eq 1 ]]; then
            echo -e "${RED}Error parsing NIC name at index $i in $CONFIG_FILE. Skipping.${NC}"
        else
            # Validation function already printed why it's invalid to stderr
            echo -e "${RED}Skipping NIC name at index $i due to invalid format.${NC}"
        fi
        ((local_errors++))
        continue # Skip to next NIC
    fi

    # If we reach here, nic_name is valid and parsed correctly.
    managed=$(safe_yq_get ".nics[$i].$nic_name.managed" "$CONFIG_FILE")
    if [[ "$managed" == "YAML_PARSE_ERROR" ]]; then
        echo -e "${RED}Error parsing managed flag for interface '$nic_name'. Skipping.${NC}"
        ((local_errors++))
        continue
    fi
    if [[ "$managed" == "false" ]]; then
        echo -e "\n${YELLOW}Skipping interface: $nic_name (managed=false)${NC}"
        continue
    fi

    echo -e "\n${BLUE}Processing interface: $nic_name${NC}"

    # Check if the physical interface actually exists first
    if ! ip link show "$nic_name" &> /dev/null; then
        echo -e "${RED}  Error: Physical interface '$nic_name' does not exist on system. Skipping creation.${NC}"
        ((local_errors++))
        continue
    fi

    # Check if a NetworkManager connection profile already exists for this interface name
    echo -e "  Checking for existing NM connection profile named '$nic_name'..."
    if nmcli connection show id "$nic_name" &> /dev/null; then
        # Command succeeded, connection exists
        echo -e "${GREEN}  Connection profile '$nic_name' already exists. Skipping creation.${NC}"
    else
        # Command failed, connection likely doesn't exist - attempt to create it
        echo -e "${YELLOW}  Connection profile '$nic_name' not found. Attempting to create...${NC}"
        # Use sudo here explicitly as nmcli modifies system config
        if sudo nmcli connection add type ethernet ifname "$nic_name" con-name "$nic_name"; then
            echo -e "${GREEN}  Successfully created NM connection profile '$nic_name'.${NC}"
        else
            echo -e "${RED}  Error: Failed to create NM connection profile '$nic_name'.${NC}"
            ((local_errors++))
        fi
    fi

done # End loop through NICs


# Final status message
if [[ "$local_errors" -gt 0 ]]; then
    echo -e "\n${RED}Finished processing, but encountered $local_errors error(s). Review logs.${NC}"
    exit 1
else
    echo -e "\n${GREEN}Successfully processed all interfaces. NetworkManager connection profiles should now exist.${NC}"
    echo -e "${YELLOW}You can now run 'configure_interfaces_v2.sh' to apply IP settings.${NC}"
    exit 0
fi
