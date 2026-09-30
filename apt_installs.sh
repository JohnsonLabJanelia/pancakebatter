#!/bin/bash

# Script for different apt installs when machine is first configured
# Jeremy Delahanty 10/02/2024

# ANSI color codes for formatting output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color (Reset)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/apt_packages.sh
source "${SCRIPT_DIR}/lib/apt_packages.sh" || exit 1

echo ""
echo "Checking if the script is being run with sudo"
echo ""

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: Please run this script as root or with sudo.${NC}"
    echo ""
    exit 1
fi

# Install recommended packages
echo ""
echo "Installing recommended packages"
echo ""

if install_apt_manifest_packages "recommended packages" base network_tools; then
    echo ""
    echo -e "${GREEN}Basic Linux packages installed successfully!${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}Package installation failed. Please check the errors above.${NC}"
    echo ""
    exit 1
fi

echo ""
echo "Installing compatible yq"
echo ""

if "${SCRIPT_DIR}/install_yq.sh"; then
    echo ""
    echo -e "${GREEN}Compatible yq installed successfully!${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}yq installation failed. Please check the errors above.${NC}"
    echo ""
    exit 1
fi

# Create the .bashrc_aliases file in the user's home directory
ALIAS_FILE=/home/$SUDO_USER/.bash_aliases

echo "Creating .bash_aliases file..."

# Build the aliases
cat <<EOL > $ALIAS_FILE
# Custom Aliases
alias ecap="sudo /opt/EVT/eCapture/eCapture"
alias sbrc="source ~/.bashrc"
alias ebrc="sudo nano ~/.bashrc"
EOL

# Check if the file was created and print its contents
if [ -f "$ALIAS_FILE" ]; then
    echo ""
    echo -e "${GREEN}Alias file created at: $ALIAS_FILE{NC}"
    echo ""
    echo "!!!"
    echo "Please apply the changes! Type: source ~/.bashrc"
    echo "!!!"
else
    echo -e "${RED}ERROR: Alias file not created!${NC}"
    exit 1
fi
