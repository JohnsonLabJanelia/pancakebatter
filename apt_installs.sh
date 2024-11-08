#!/bin/bash

# Script for different apt installs when machine is first configured
# Jeremy Delahanty 10/02/2024

# ANSI color codes for formatting output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color (Reset)

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

# Update apt repository
echo ""
echo "Updating apt repository"
echo ""

sudo apt update

# Check if apt update succeeded
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}Apt repository updated successfully!${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}Apt update failed. Please check the errors above.${NC}"
    echo ""
    exit 1
fi

# Install recommended packages
echo ""
echo "Installing recommended packages"
echo ""

# Execute installation
sudo apt install -y build-essential plocate autofs vim gcc make \
    pkg-config libglvnd-dev smartmontools \
    git openssh-server openssh-client \
    libxcb-xinerama0 filezilla dkms \
    tmux htop curl wget arping sysstat \
    libglfw3 libglfw3-dev libglew-dev \
    tree yasm cmake libtool libc6 libc6-dev \
    unzip libnuma1 libnuma-dev \
    nasm libx264-dev libxext-dev libxfixes-dev \
    zlib1g-dev libeigen3-dev libgflags-dev libgoogle-glog-dev \
    automake autoconf patchelf lm-sensors fio \
    nvme-cli


# Check if the installation was successful
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}Basic Linux packages installed successfully!${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}Package installation failed. Please check the errors above.${NC}"
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
