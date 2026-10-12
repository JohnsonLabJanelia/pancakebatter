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

# GitHub's apt repository, so sysadmin.txt's gh is current rather than jammy's 2.4.0
echo ""
echo "Adding the GitHub CLI apt repository"
echo ""

if ! "${SCRIPT_DIR}/install_gh_apt_repo.sh"; then
    echo ""
    echo -e "${RED}GitHub CLI repository setup failed. Please check the errors above.${NC}"
    echo ""
    exit 1
fi

# Install recommended packages
echo ""
echo "Installing recommended packages"
echo ""

if install_apt_manifest_packages "recommended packages" base network_tools build_tools sysadmin data_diagnostics python; then
    echo ""
    echo -e "${GREEN}Basic Linux packages installed successfully!${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}Package installation failed. Please check the errors above.${NC}"
    echo ""
    exit 1
fi

# Git LFS filters for every user (/etc/gitconfig), not just root's ~/.gitconfig; --skip-repo
# keeps it from installing hooks and a root-owned .git/lfs into this checkout
echo ""
echo "Enabling Git LFS for all users"
echo ""

if git lfs install --system --skip-repo; then
    echo ""
    echo -e "${GREEN}Git LFS enabled system-wide!${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}git lfs install --system --skip-repo failed. Please check the errors above.${NC}"
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
