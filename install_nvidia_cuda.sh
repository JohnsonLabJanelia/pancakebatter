#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color (Reset)

# CUDA installer details
CUDA_VERSION="12.2.2"
CUDA_INSTALLER="cuda_${CUDA_VERSION}_535.104.05_linux.run"
CUDA_URL="https://developer.download.nvidia.com/compute/cuda/${CUDA_VERSION}/local_installers/${CUDA_INSTALLER}"

# Function to print colored output
print_color() {
    local color=$1
    local message=$2
    echo -e "${color}${message}${NC}"
}

# Function to find nvcc in default user installation directory
find_nvcc() {
    if command -v nvcc &> /dev/null; then
        return 0
    elif [ -x "/usr/local/cuda/bin/nvcc" ]; then
        return 0
    else
        return 1
    fi
}

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    print_color "$RED" "ERROR: Please run this script as sudo."
    print_color "$RED" "This script requires root permissions to install CUDA."
    exit 1
fi

# Check for NVIDIA driver
print_color "$YELLOW" "Checking NVIDIA driver..."
if command -v nvidia-smi &> /dev/null; then
    print_color "$GREEN" "NVIDIA driver found. Version info:"
    nvidia-smi --query-gpu=driver_version --format=csv,noheader
else
    print_color "$RED" "ERROR: NVIDIA driver not found. Please install it first."
    print_color "$YELLOW" "Refer to the readme for instructions on installing the NVIDIA driver."
    exit 1
fi

# Check if CUDA is already installed
if find_nvcc; then
    print_color "$YELLOW" "CUDA is already installed. Current version:"
    if command -v nvcc &> /dev/null; then
        nvcc --version
    else
        /usr/local/cuda/bin/nvcc --version
    fi
    read -p "Do you want to continue with the installation? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        print_color "$YELLOW" "Installation aborted by user."
        exit 0
    fi
fi

# Download CUDA installer
print_color "$YELLOW" "Downloading CUDA installer..."
if wget "$CUDA_URL"; then
    print_color "$GREEN" "CUDA installer downloaded successfully."
else
    print_color "$RED" "ERROR: Failed to download CUDA installer."
    exit 1
fi

# Make the installer executable
chmod +x "$CUDA_INSTALLER"

# Run CUDA installer silently
print_color "$YELLOW" "Installing CUDA..."
if ./"$CUDA_INSTALLER" --toolkit --silent; then
    print_color "$GREEN" "CUDA installation completed successfully."
else
    print_color "$RED" "ERROR: CUDA installation failed."
    exit 1
fi

# Update library path
print_color "$YELLOW" "Updating library path..."
ldconfig
print_color "$GREEN" "Library path updated."

# Update PATH and environment variables
print_color "$YELLOW" "Updating PATH and environment variables..."
export PATH="/usr/local/cuda-${CUDA_VERSION}/bin:$PATH"
export LD_LIBRARY_PATH="/usr/local/cuda-${CUDA_VERSION}/lib64:$LD_LIBRARY_PATH"

# Source CUDA environment variables
if [ -f "/usr/local/cuda-${CUDA_VERSION}/env.sh" ]; then
    source "/usr/local/cuda-${CUDA_VERSION}/env.sh"
    print_color "$GREEN" "CUDA environment variables sourced."
else
    print_color "$YELLOW" "CUDA environment file not found. This may be normal for some installations."
fi

# Verify CUDA installation
print_color "$YELLOW" "Verifying CUDA installation..."
if find_nvcc && (/usr/local/cuda/bin/nvcc --version 2>/dev/null || nvcc --version 2>/dev/null) | grep -q "$CUDA_VERSION"; then
    print_color "$GREEN" "CUDA $CUDA_VERSION is successfully installed and accessible."
    if command -v nvcc &> /dev/null; then
        nvcc --version
    else
        /usr/local/cuda/bin/nvcc --version
    fi
else
    print_color "$RED" "ERROR: CUDA installation verification failed."
    print_color "$YELLOW" "Current PATH: $PATH"
    print_color "$YELLOW" "Current LD_LIBRARY_PATH: $LD_LIBRARY_PATH"
    print_color "$YELLOW" "NOTE: If verification fails, try reopening your terminal or logging out and back in."
    print_color "$YELLOW" "Then run 'nvcc --version' manually to check if CUDA is accessible."
    exit 1
fi


# Clean up installer file
print_color "$YELLOW" "Cleaning up..."
if rm "$CUDA_INSTALLER"; then
    print_color "$GREEN" "CUDA installer file removed."
else
    print_color "$YELLOW" "WARNING: Failed to remove CUDA installer file."
    ls -l "$CUDA_INSTALLER"
fi

# Load nvidia-peer-memory module
print_color "$YELLOW" "Loading nvidia-peer-memory module..."
if update-rc.d start-nvidia-peermem defaults && /etc/init.d/start-nvidia-peermem start; then
    print_color "$GREEN" "nvidia-peer-memory module loaded successfully."
else
    print_color "$RED" "ERROR: Failed to load nvidia-peer-memory module."
    print_color "$YELLOW" "This might be normal if the module is not installed or not needed for your setup."
fi

# Verify nvidia-peer-memory module
if lsmod | grep -q nvidia_peermem; then
    print_color "$GREEN" "nvidia-peer-memory module verified."
else
    print_color "$YELLOW" "NOTE: nvidia-peer-memory module not loaded. This might be normal for some setups."
fi

print_color "$GREEN" "CUDA installation and setup completed successfully!"
print_color "$YELLOW" "Please run 'nvcc --version' in a new terminal to verify the installation."

exit 0