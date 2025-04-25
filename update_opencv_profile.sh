#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;36m'
NC='\033[0m'

OPENCV_DIR="/opt/orange/lib/opencv"
SYSTEM_PKGCONFIG="/usr/lib/x86_64-linux-gnu/pkgconfig"

echo -e "${BLUE}Creating system-wide pkg-config link for OpenCV...${NC}"

# First ensure the OpenCV pkg-config file exists
if [ ! -f "${OPENCV_DIR}/lib/pkgconfig/opencv4.pc" ]; then
    echo -e "${RED}OpenCV pkg-config file not found in ${OPENCV_DIR}/lib/pkgconfig/${NC}"
    exit 1
fi

# Create symbolic link in system pkgconfig directory
sudo ln -sf "${OPENCV_DIR}/lib/pkgconfig/opencv4.pc" "${SYSTEM_PKGCONFIG}/opencv4.pc"

# Update the sudoers configuration to preserve PKG_CONFIG_PATH
SUDOERS_FILE="/etc/sudoers.d/preserve_pkg_config"
echo -e "${BLUE}Updating sudoers to preserve PKG_CONFIG_PATH...${NC}"

echo 'Defaults env_keep += "PKG_CONFIG_PATH"' | sudo EDITOR='tee' visudo -f "$SUDOERS_FILE"

# Verify the installation
echo -e "${BLUE}Verifying pkg-config setup...${NC}"

# Test as regular user
echo -e "${YELLOW}Testing as regular user:${NC}"
if pkg-config --exists opencv4; then
    echo -e "${GREEN}✓ OpenCV pkg-config found (user context)${NC}"
    echo -e "Version: $(pkg-config --modversion opencv4)"
else
    echo -e "${RED}✗ OpenCV pkg-config not found (user context)${NC}"
fi

# Test as sudo
echo -e "\n${YELLOW}Testing as sudo:${NC}"
if sudo pkg-config --exists opencv4; then
    echo -e "${GREEN}✓ OpenCV pkg-config found (sudo context)${NC}"
    echo -e "Version: $(sudo pkg-config --modversion opencv4)"
else
    echo -e "${RED}✗ OpenCV pkg-config not found (sudo context)${NC}"
fi

echo -e "\n${YELLOW}Note: Please run your dependency check script again to verify the fix.${NC}"