#!/bin/bash

# FFmpeg Uninstall Script
# Created by Claude for Jeremy Delahanty, October 2024

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run this script with sudo${NC}"
    exit 1
fi

# Installation directories to clean
INSTALL_DIR="/opt/orange"
FFMPEG_DIR="${INSTALL_DIR}/lib/ffmpeg-nvidia"

# Environment file
ENV_FILE="/etc/profile.d/ffmpeg-orange.sh"

echo -e "${YELLOW}Starting FFmpeg uninstallation...${NC}"

# Remove environment variables file
if [ -f "$ENV_FILE" ]; then
    echo "Removing environment configuration file..."
    rm -f "$ENV_FILE"
fi

# Remove FFmpeg installation directory
if [ -d "$FFMPEG_DIR" ]; then
    echo "Removing FFmpeg installation..."
    rm -rf "$FFMPEG_DIR"
fi

# Clean pkg-config
if [ -d "/usr/local/lib/pkgconfig" ]; then
    echo "Cleaning pkgconfig files..."
    rm -f /usr/local/lib/pkgconfig/ffnvcodec.pc
fi

# Remove nv-codec-headers
echo "Removing nv-codec-headers..."
rm -f /usr/local/include/ffnvcodec/*.h

echo -e "${GREEN}FFmpeg uninstallation completed successfully!${NC}"
echo "Please log out and log back in for environment changes to take effect"

exit 0