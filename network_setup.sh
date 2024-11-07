#!/bin/bash

# Script for setting up network configurations on rig machines
# Jeremy Delahanty, Claude Sonnet 3.5 10/02/2024

# ANSI color codes for formatting output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color (Reset)

echo -e "${YELLOW}Checking if the script is being run with sudo${NC}"

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: Please run this script as root or with sudo.${NC}"
    exit 1
fi

# Function to check and create blacklist file
check_blacklist() {
    echo -e "\n${YELLOW}Checking WiFi blacklist configuration...${NC}"
    if [ -f "/etc/modprobe.d/blacklist-wifi.conf" ]; then
        echo -e "${GREEN}WiFi blacklist file already exists${NC}"
    else
        echo -e "${YELLOW}Creating WiFi blacklist file...${NC}"
        cat << EOF > /etc/modprobe.d/blacklist-wifi.conf
blacklist iwlwifi
blacklist iwldvm
blacklist iwlmvm
EOF
        echo -e "${GREEN}WiFi blacklist file created successfully${NC}"
    fi
}

update_initramfs() {
    echo -e "\n${YELLOW}Updating initramfs...${NC}"
    echo ""
    if update-initramfs -u 2>&1 | tee /tmp/initramfs_update.log; then
        if grep -qi "error" /tmp/initramfs_update.log; then
            echo -e "${RED}Error detected during initramfs update. Check /tmp/initramfs_update.log for details.${NC}"
            return 1
        else
            echo -e "${GREEN}initramfs updated successfully${NC}"
            return 0
        fi
    else
        echo -e "${RED}Failed to update initramfs. Check /tmp/initramfs_update.log for details.${NC}"
        return 1
    fi
}

# Check if wireless adapter is enabled
echo -e "\n${YELLOW}Checking if the wireless adapter is enabled...${NC}"
echo ""

if ip link show | grep -q wlp; then
    echo -e "${RED}Wireless adapter is still enabled${NC}"
    check_networkmanager_conf
    check_blacklist
    if update_initramfs; then
        echo -e "\n${YELLOW}Configuration complete. Please reboot the system for changes to take effect.${NC}"
    else
        echo -e "\n${RED}Configuration incomplete due to initramfs update failure. Please check the logs and try again.${NC}"
    fi
else
    echo -e "${GREEN}Wireless adapter is already disabled${NC}"
fi

echo -e "\n${GREEN}Script execution complete${NC}"
echo ""
