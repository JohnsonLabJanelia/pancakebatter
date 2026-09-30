#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/apt_packages.sh
source "${SCRIPT_DIR}/lib/apt_packages.sh" || exit 1

# Check if the script is run as root
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}Error: This script must be run as root. Please use sudo.${NC}"
  exit 1
fi

# Install necessary dependencies
echo -e "${YELLOW}Updating package list and installing dependencies...${NC}"
install_apt_manifest_packages "ENet build dependencies" enet || exit 1

# Download ENet source code
echo -e "${YELLOW}Downloading ENet source code...${NC}"
wget https://github.com/lsalzman/enet/archive/refs/tags/v1.3.18.tar.gz -O enet-1.3.18.tar.gz

# Extract the downloaded tar.gz file
echo -e "${YELLOW}Extracting ENet source code...${NC}"
tar -xzf enet-1.3.18.tar.gz
cd enet-1.3.18

# Generate build system
echo -e "${YELLOW}Generating build system...${NC}"
autoreconf -vfi

# Build and install ENet
echo -e "${YELLOW}Building and installing ENet...${NC}"
./configure && make && make install

# Clean up
echo -e "${YELLOW}Cleaning up...${NC}"
cd ..
rm -rf enet-1.3.18 enet-1.3.18.tar.gz

# Installation complete
echo -e "${GREEN}ENet installation complete!${NC}"

exit 0
