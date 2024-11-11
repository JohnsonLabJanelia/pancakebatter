#!/bin/bash

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No color

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CONFIG_FILE="${SCRIPT_DIR}/system_config.yml"
UDEV_RULES_FILE="/etc/udev/rules.d/10-network-aliases.rules"

# Validation functions
validate_mac_address() {
    local mac=$1
    local valid_mac_regex="^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$"
    if [[ ! $mac =~ $valid_mac_regex ]]; then
        echo -e "${RED}Invalid MAC address format: $mac${NC}"
        return 1
    fi
    return 0
}

validate_ip_address() {
    local ip=$1
    # Remove CIDR notation for validation
    local ip_addr=${ip%/*}
    local cidr=${ip#*/}
    
    # Validate IP format
    local valid_ip_regex="^([0-9]{1,3}\.){3}[0-9]{1,3}$"
    if [[ ! $ip_addr =~ $valid_ip_regex ]]; then
        echo -e "${RED}Invalid IP address format: $ip_addr${NC}"
        return 1
    fi
    
    # Validate each octet
    local IFS='.'
    read -r -a octets <<< "$ip_addr"
    for octet in "${octets[@]}"; do
        if [ "$octet" -gt 255 ] || [ "$octet" -lt 0 ]; then
            echo -e "${RED}Invalid IP octet value: $octet${NC}"
            return 1
        fi
    done
    
    # Validate CIDR
    if [ "$cidr" -lt 1 ] || [ "$cidr" -gt 32 ]; then
        echo -e "${RED}Invalid CIDR notation: $cidr${NC}"
        return 1
    fi
    
    return 0
}

validate_mtu() {
    local mtu=$1
    # Standard MTU range: 68 to 65536
    if ! [[ "$mtu" =~ ^[0-9]+$ ]] || [ "$mtu" -lt 68 ] || [ "$mtu" -gt 65536 ]; then
        echo -e "${RED}Invalid MTU value: $mtu (must be between 68 and 65536)${NC}"
        return 1
    fi
    return 0
}

validate_speed() {
    local speed=$1
    # Valid speeds: 1000, 10000, 25000, 40000, 100000 (Mbps)
    local valid_speeds=(1000 10000 25000 40000 100000)
    local valid=0
    for valid_speed in "${valid_speeds[@]}"; do
        if [ "$speed" -eq "$valid_speed" ]; then
            valid=1
            break
        fi
    done
    if [ $valid -eq 0 ]; then
        echo -e "${RED}Invalid speed value: $speed (must be one of: ${valid_speeds[*]} Mbps)${NC}"
        return 1
    fi
    return 0
}

validate_interface_name() {
    local name=$1
    # Interface name must be alphanumeric and no longer than 15 characters
    local valid_name_regex="^[a-zA-Z0-9_-]{1,15}$"
    if [[ ! $name =~ $valid_name_regex ]]; then
        echo -e "${RED}Invalid interface name: $name${NC}"
        return 1
    fi
    return 0
}

check_ip_conflicts() {
    local ip=$1
    local interface=$2
    local ip_addr=${ip%/*}
    
    # Check if IP is already assigned to a different interface
    local existing_interface=$(ip addr show | grep -B2 "$ip_addr" | grep -v "$interface" | grep "^[0-9]" | awk -F: '{print $2}' | tr -d ' ')
    if [ -n "$existing_interface" ]; then
        echo -e "${RED}IP address $ip_addr is already assigned to interface $existing_interface${NC}"
        return 1
    fi
    return 0
}

validate_nic_config() {
    local name=$1
    local mac=$2
    local altname=$3
    local ip=$4
    local mtu=$5
    local speed=$6
    local errors=0

    echo -e "${BLUE}Validating configuration for NIC: $name${NC}"
    
    # Validate MAC address
    if ! validate_mac_address "$mac"; then
        ((errors++))
    else
        echo -e "${GREEN}✓ MAC address format is valid${NC}"
    fi
    
    # Validate interface name
    if ! validate_interface_name "$altname"; then
        ((errors++))
    else
        echo -e "${GREEN}✓ Interface name format is valid${NC}"
    fi
    
    # Validate IP address
    if ! validate_ip_address "$ip"; then
        ((errors++))
    else
        echo -e "${GREEN}✓ IP address format is valid${NC}"
        # Check for IP conflicts only if format is valid
        if ! check_ip_conflicts "$ip" "$altname"; then
            ((errors++))
        else
            echo -e "${GREEN}✓ No IP conflicts detected${NC}"
        fi
    fi
    
    # Validate MTU
    if ! validate_mtu "$mtu"; then
        ((errors++))
    else
        echo -e "${GREEN}✓ MTU value is valid${NC}"
    fi
    
    # Validate Speed
    if ! validate_speed "$speed"; then
        ((errors++))
    else
        echo -e "${GREEN}✓ Speed value is valid${NC}"
    fi
    
    return $errors
}

# Function to clean up existing IP addresses
clean_interface_ips() {
    local interface=$1
    echo -e "${BLUE}Cleaning existing IPs from $interface${NC}"
    
    if ip addr flush dev "$interface" scope global; then
        echo -e "${GREEN}  Successfully cleaned IPs from $interface${NC}"
    else
        echo -e "${RED}  Failed to clean IPs from $interface${NC}"
        return 1
    fi
}

# Function to create udev rules for interface naming
create_udev_rules() {
    local mac=$1
    local name=$2
    local altname=$3
    
    echo -e "${GREEN}Adding udev rule for MAC address $mac -> $name (alt: $altname)${NC}"
    
    # Create the primary interface name rule
    echo "SUBSYSTEM==\"net\", ACTION==\"add\", ATTR{address}==\"$mac\", NAME=\"$name\"" >> "$UDEV_RULES_FILE"
    
    # Create symlink for alternative name
    echo "SUBSYSTEM==\"net\", ACTION==\"add\", ATTR{address}==\"$mac\", SYMLINK+=\"$altname\"" >> "$UDEV_RULES_FILE"
}

# Main script execution starts here
echo -e "${YELLOW}Starting network interface configuration...${NC}"

# Debug information
echo -e "${YELLOW}Debug Information:${NC}"
echo "Script Directory: $SCRIPT_DIR"
echo "Config File Path: $CONFIG_FILE"
echo "Current User: $(whoami)"
echo "SUDO_USER: $SUDO_USER"
ls -l "$CONFIG_FILE"

# Check if config file exists and is readable
if [ ! -f "$CONFIG_FILE" ]; then
    echo -e "${RED}Error: Config file not found at $CONFIG_FILE${NC}"
    exit 1
fi

# Create udev rules file for network aliases
echo -e "${YELLOW}Creating udev rules file at $UDEV_RULES_FILE...${NC}"
echo '# Custom udev rules for network interface naming and aliases' > "$UDEV_RULES_FILE"

# Empty the rules file if it exists, or create a new one
echo '# Custom udev rules for network alias assignment' > "$UDEV_RULES_FILE"

# Check required tools
for tool in yq ethtool ip; do
    if ! command -v $tool &> /dev/null; then
        echo -e "${RED}Error: Required tool '$tool' is not installed${NC}"
        exit 1
    fi
done

# Get the length of the nics array
nics_length=$(sudo -u "$SUDO_USER" yq e '.nics | length' "$CONFIG_FILE")
validation_errors=0

# First pass: Validate all configurations
echo -e "${BLUE}Validating all NIC configurations...${NC}"
for ((i=0; i<$nics_length; i++)); do
    nic_name=$(sudo -u "$SUDO_USER" yq e ".nics[$i] | keys | .[0]" "$CONFIG_FILE")
    mac_address=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.mac_address" "$CONFIG_FILE" | tr '[:upper:]' '[:lower:]')
    altname=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.altname" "$CONFIG_FILE")
    ip_address=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.ip_address" "$CONFIG_FILE")
    mtu=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.mtu" "$CONFIG_FILE")
    speed=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.link_settings.speed" "$CONFIG_FILE")
    
    echo -e "\n${YELLOW}Validating NIC: $nic_name${NC}"
    if ! validate_nic_config "$nic_name" "$mac_address" "$altname" "$ip_address" "$mtu" "$speed"; then
        ((validation_errors++))
    fi
done

# Check if there were any validation errors
if [ $validation_errors -gt 0 ]; then
    echo -e "${RED}Found $validation_errors validation error(s). Please fix the configuration and try again.${NC}"
    exit 1
fi

# If all validations passed, proceed with configuration
echo -e "${GREEN}All configurations validated successfully. Proceeding with interface configuration...${NC}"

# Iterate through the nics array
for ((i=0; i<$nics_length; i++)); do
    # Get the NIC name (key of the first map in the object)
    nic_name=$(sudo -u "$SUDO_USER" yq e ".nics[$i] | keys | .[0]" "$CONFIG_FILE")
    
    # Extract values using the correct path
    mac_address=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.mac_address" "$CONFIG_FILE" | tr '[:upper:]' '[:lower:]')
    altname=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.altname" "$CONFIG_FILE")
    ip_address=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.ip_address" "$CONFIG_FILE")
    mtu=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.mtu" "$CONFIG_FILE")
    speed=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.link_settings.speed" "$CONFIG_FILE")
    autoneg=$(sudo -u "$SUDO_USER" yq e ".nics[$i].$nic_name.link_settings.autoneg" "$CONFIG_FILE")
    
    # Debug output
    echo -e "${YELLOW}Found NIC:${NC}"
    echo "  Name: $nic_name"
    echo "  MAC: $mac_address"
    echo "  Altname: $altname"
    echo "  IP: $ip_address"
    echo "  MTU: $mtu"
    echo "  Speed: $speed"
    echo "  AutoNeg: $autoneg"

    # Skip if any required fields are missing or null
    if [[ -n "$altname" && -n "$mac_address" && "$mac_address" != "null" ]]; then
        echo -e "${GREEN}Adding udev rule for MAC address $mac_address -> $nic_name (alt: $altname)${NC}"
        # Create the primary interface name rule
        echo "SUBSYSTEM==\"net\", ACTION==\"add\", ATTR{address}==\"$mac_address\", NAME=\"$nic_name\"" >> "$UDEV_RULES_FILE"
        # Create symlink for alternative name
        echo "SUBSYSTEM==\"net\", ACTION==\"add\", ATTR{address}==\"$mac_address\", SYMLINK+=\"$altname\"" >> "$UDEV_RULES_FILE"
        
        # Store settings for later configuration
        if [[ -n "$ip_address" && "$ip_address" != "null" ]]; then
            declare "NIC_IP_$nic_name=$ip_address"
        fi
        if [[ -n "$mtu" && "$mtu" != "null" ]]; then
            declare "NIC_MTU_$nic_name=$mtu"
        fi
        if [[ -n "$speed" && "$speed" != "null" ]]; then
            declare "NIC_SPEED_$nic_name=$speed"
        fi
        declare "NIC_AUTONEG_$nic_name=$autoneg"
    fi
done

# After writing all udev rules, prompt for reboot instead of configuring
echo -e "${YELLOW}Network interface rules have been written to $UDEV_RULES_FILE${NC}"
echo -e "${YELLOW}To apply these changes safely, a reboot is required.${NC}"
echo -e "${YELLOW}Would you like to:${NC}"
echo "1. Save changes and reboot now"
echo "2. Save changes only (manual reboot required)"
read -p "Enter choice (1 or 2): " choice

case $choice in
    1)
        echo -e "${GREEN}Rebooting system...${NC}"
        sync  # Ensure all changes are written to disk
        systemctl reboot
        ;;
    2)
        echo -e "${GREEN}Changes saved. Please reboot your system when convenient.${NC}"
        echo -e "${YELLOW}After reboot, run this script again to configure interface settings.${NC}"
        ;;
    *)
        echo -e "${RED}Invalid choice. Please reboot manually when convenient.${NC}"
        ;;
esac

exit 0