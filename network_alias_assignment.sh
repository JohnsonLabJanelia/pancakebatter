#!/bin/bash

# Updated script to set up network interface aliases
# Created by Claude (Anthropic AI) for Jeremy Delahanty, 10/02/2024

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Please run as root${NC}"
  exit 1
fi

# Function to get MAC address
get_mac_address() {
  ip link show $1 | awk '/ether/ {print $2}'
}

# Create udev rule file
rule_file="/etc/udev/rules.d/10-network-aliases.rules"

echo -e "${YELLOW}Creating udev rules file: $rule_file${NC}"
> $rule_file

# Define aliases for each interface
# The aliases for enp65s0 are for the Emergent NIC
# The order of these aliases from top to bottom match
# the ports from top of machine to the bottom
declare -A aliases
# Motherboard ethernet configurations
aliases[enp36s0f0]="eth_internet1"
aliases[enp36s0f1]="eth_internet2"
# Emergent NIC configurations
aliases[enp65s0d3]="camera4_25Gb"
aliases[enp65s0d1]="camera3_25Gb"
aliases[enp65s0]="camera2_25Gb"
aliases[enp65s0d2]="camera1_25Gb"

# Process each interface
for interface in "${!aliases[@]}"; do
  mac=$(get_mac_address $interface)
  if [ -z "$mac" ]; then
    echo -e "${YELLOW}Interface $interface not found, skipping${NC}"
    continue
  fi
  
  alias=${aliases[$interface]}
  echo -e "${GREEN}Setting alias for $interface ($mac) to $alias${NC}"
  echo "SUBSYSTEM==\"net\", ACTION==\"add\", ATTR{address}==\"$mac\", NAME=\"$alias\"" >> $rule_file
done

echo -e "${GREEN}Rules file created. Please reboot your system for changes to take effect.${NC}"
echo -e "${YELLOW}After reboot, use 'ip addr show' to verify the new interface names.${NC}"
