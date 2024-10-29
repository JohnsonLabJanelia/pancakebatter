#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if the script is running as root
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}Error: This script must be run as root. Please use sudo.${NC}"
    exit 1
fi

# URL for TensorRT package
tensorrt_url="https://developer.nvidia.com/downloads/compute/machine-learning/tensorrt/10.0.1/tars/TensorRT-10.0.1.6.Linux.x86_64-gnu.cuda-12.4.tar.gz"

# Filename to save
tensorrt_tar="TensorRT-10.0.1.6.Linux.x86_64-gnu.cuda-12.4.tar.gz"

# Download the TensorRT tarball
echo -e "${YELLOW}Downloading TensorRT package...${NC}"
wget -O $tensorrt_tar "$tensorrt_url"

# Check if download was successful
if [ $? -ne 0 ]; then
    echo -e "${RED}Error: Failed to download TensorRT package.${NC}"
    exit 1
fi

# Extract the TensorRT tarball
echo -e "${YELLOW}Extracting TensorRT package...${NC}"
tar -xzf $tensorrt_tar -C /usr/local/

# Clean up downloaded file
rm -f $tensorrt_tar

# Set up environment variables for TensorRT
echo -e "${YELLOW}Setting up environment variables...${NC}"
export LD_LIBRARY_PATH=$LD_LIBRARY_PATH:/usr/local/TensorRT-10.0.1.6/lib

# Add environment variables to bashrc
echo "export LD_LIBRARY_PATH=\$LD_LIBRARY_PATH:/usr/local/TensorRT-10.0.1.6/lib" >> ~/.bashrc

echo -e "${GREEN}TensorRT installation completed successfully!${NC}"

# Uninstall TensorRT function
uninstall_tensorrt() {
    echo -e "${YELLOW}Uninstalling TensorRT package...${NC}"
    rm -rf /usr/local/TensorRT-10.0.1.6
    sed -i '/\/usr\/local\/TensorRT-10.0.1.6\/lib/d' ~/.bashrc
    echo -e "${GREEN}TensorRT uninstalled successfully!${NC}"
}

# Check for uninstall argument
if [ "$1" == "uninstall" ]; then
    uninstall_tensorrt
    exit 0
fi

exit 0
