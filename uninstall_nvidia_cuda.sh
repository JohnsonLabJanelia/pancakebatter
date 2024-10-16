#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color (Reset)

# CUDA version (should match your installation)
CUDA_VERSION="12.2"

# Function to print colored output
print_color() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    print_color "$RED" "ERROR: Please run this script as sudo."
    print_color "$RED" "This script requires root permissions to uninstall CUDA."
    exit 1
fi

# Check if CUDA is installed
if [ ! -d "/usr/local/cuda-${CUDA_VERSION}" ]; then
    print_color "$RED" "Error: CUDA ${CUDA_VERSION} installation not found in /usr/local/cuda-${CUDA_VERSION}"
    exit 1
fi

# Find the uninstall script
UNINSTALL_SCRIPT="/usr/local/cuda-${CUDA_VERSION}/bin/cuda-uninstaller"
if [ ! -f "$UNINSTALL_SCRIPT" ]; then
    print_color "$RED" "Error: CUDA uninstaller not found at $UNINSTALL_SCRIPT"
    exit 1
fi

# Confirm with the user
print_color "$YELLOW" "WARNING: This will uninstall CUDA ${CUDA_VERSION} from your system."
read -p "Are you sure you want to proceed? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    print_color "$YELLOW" "Uninstallation aborted by user."
    exit 0
fi

# Run the uninstaller
print_color "$YELLOW" "Running CUDA uninstaller..."
if $UNINSTALL_SCRIPT; then
    print_color "$GREEN" "CUDA ${CUDA_VERSION} has been uninstalled successfully."
else
    print_color "$RED" "Error: CUDA uninstallation failed."
    exit 1
fi

# Remove CUDA from PATH and LD_LIBRARY_PATH in .bashrc or .bash_profile
print_color "$YELLOW" "Removing CUDA paths from .bashrc and .bash_profile..."
sed -i '/export PATH=\/usr\/local\/cuda/d' ~/.bashrc ~/.bash_profile
sed -i '/export LD_LIBRARY_PATH=\/usr\/local\/cuda/d' ~/.bashrc ~/.bash_profile

# Remove any remaining CUDA directories
if [ -d "/usr/local/cuda-${CUDA_VERSION}" ]; then
    print_color "$YELLOW" "Removing remaining CUDA directory..."
    rm -rf "/usr/local/cuda-${CUDA_VERSION}"
fi

if [ -L "/usr/local/cuda" ]; then
    print_color "$YELLOW" "Removing CUDA symlink..."
    rm "/usr/local/cuda"
fi

# Update library cache
print_color "$YELLOW" "Updating library cache..."
ldconfig

print_color "$GREEN" "CUDA ${CUDA_VERSION} has been uninstalled and system cleaned up."
print_color "$YELLOW" "Please log out and log back in for all changes to take effect."
print_color "$YELLOW" "You may also need to manually remove any CUDA-related environment variables from other configuration files."

exit 0