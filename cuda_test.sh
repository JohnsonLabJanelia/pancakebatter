#!/bin/bash

# FFmpeg Configure Debug Script
# Created by Claude for Jeremy Delahanty, October 2024

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/apt_packages.sh
source "${SCRIPT_DIR}/lib/apt_packages.sh" || exit 1

# Build directories (temporary)
BUILD_DIR="/tmp/ffmpeg-build-$$"  # Using $$ (PID) to make unique
FFMPEG_BUILD="${BUILD_DIR}/ffmpeg"
NVCODEC_BUILD="${BUILD_DIR}/nv-codec-headers"

# Final installation directories (system-wide)
INSTALL_DIR="/opt/orange"
FFMPEG_DIR="${INSTALL_DIR}/lib/ffmpeg"
NVIDIA_DIR="${INSTALL_DIR}/lib/ffmpeg-nvidia"

# Function to log and exit on error
error_exit() {
    echo -e "${RED}Error: $1${NC}" >&2
    echo -e "${YELLOW}Build directory preserved at: $BUILD_DIR${NC}"
    echo -e "${YELLOW}Check $FFMPEG_BUILD/ffbuild/config.log for details${NC}"
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error_exit "Please run this script with sudo"
fi

# Set up CUDA environment
CUDA_PATH="/usr/local/cuda"
export PATH="$CUDA_PATH/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_PATH/lib64:$LD_LIBRARY_PATH"
export CUDA_HOME="$CUDA_PATH"
export CUDA_ROOT="$CUDA_PATH"
export CUDA_PATH_V12_2="$CUDA_PATH"
export CUDA_TOOLKIT_ROOT_DIR="$CUDA_PATH"
export PKG_CONFIG_PATH="$CUDA_PATH/lib64/pkgconfig:/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"

# Verify CUDA environment
echo -e "${BLUE}Verifying CUDA installation...${NC}"
if ! command -v nvcc &> /dev/null; then
    error_exit "nvcc not found in PATH"
fi

echo "CUDA environment:"
echo "PATH=$PATH"
echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
echo "CUDA_HOME=$CUDA_HOME"
echo "CUDA_ROOT=$CUDA_ROOT"
echo "CUDA_PATH_V12_2=$CUDA_PATH_V12_2"
echo "CUDA_TOOLKIT_ROOT_DIR=$CUDA_TOOLKIT_ROOT_DIR"
echo "PKG_CONFIG_PATH=$PKG_CONFIG_PATH"

nvcc --version || error_exit "Failed to run nvcc"

# Create build directories
echo -e "${BLUE}Creating build directories...${NC}"
mkdir -p "$BUILD_DIR" || error_exit "Failed to create build directory"
chown $SUDO_USER:$SUDO_USER "$BUILD_DIR"

# Install dependencies
echo -e "${YELLOW}Installing dependencies...${NC}"
install_apt_manifest_packages "CUDA debug dependencies" cuda_test \
    || error_exit "Failed to install dependencies"

# Clone and build nv-codec-headers
echo -e "${YELLOW}Building nv-codec-headers...${NC}"
cd "$BUILD_DIR" || error_exit "Failed to change to build directory"
sudo -u $SUDO_USER mkdir -p "$NVCODEC_BUILD"
cd "$NVCODEC_BUILD" || error_exit "Failed to change to NVCODEC directory"
sudo -u $SUDO_USER git clone https://git.videolan.org/git/ffmpeg/nv-codec-headers.git . || error_exit "Failed to clone nv-codec-headers"
make install || error_exit "Failed to install nv-codec-headers"

# Get FFmpeg source
echo -e "${YELLOW}Getting FFmpeg source...${NC}"
sudo -u $SUDO_USER mkdir -p "$FFMPEG_BUILD"
cd "$FFMPEG_BUILD" || error_exit "Failed to change to FFmpeg directory"
sudo -u $SUDO_USER git clone https://git.ffmpeg.org/ffmpeg.git . || error_exit "Failed to clone FFmpeg"
sudo -u $SUDO_USER git checkout release/4.4 || error_exit "Failed to checkout release 4.4"

# Create a test CUDA program to verify compilation
echo -e "${YELLOW}Testing CUDA compilation in build environment...${NC}"
TEST_DIR="$BUILD_DIR/cuda_test"
sudo -u $SUDO_USER mkdir -p "$TEST_DIR"
cat > "$TEST_DIR/test.cu" << 'EOF'
#include <stdio.h>
int main() {
    printf("CUDA test successful!\n");
    return 0;
}
EOF
cd "$TEST_DIR"
sudo -u $SUDO_USER env \
    PATH="$PATH" \
    LD_LIBRARY_PATH="$LD_LIBRARY_PATH" \
    CUDA_HOME="$CUDA_HOME" \
    nvcc test.cu -o test || error_exit "CUDA test compilation failed"
./test || error_exit "CUDA test execution failed"
cd "$FFMPEG_BUILD"

# Configure FFmpeg with verbose output and updated CUDA architecture flags
echo -e "${YELLOW}Configuring FFmpeg...${NC}"
sudo -u $SUDO_USER env \
    PATH="$PATH" \
    LD_LIBRARY_PATH="$LD_LIBRARY_PATH" \
    CUDA_HOME="$CUDA_HOME" \
    CUDA_ROOT="$CUDA_ROOT" \
    CUDA_PATH_V12_2="$CUDA_PATH_V12_2" \
    CUDA_TOOLKIT_ROOT_DIR="$CUDA_TOOLKIT_ROOT_DIR" \
    PKG_CONFIG_PATH="$PKG_CONFIG_PATH" \
    ./configure \
    --prefix="${NVIDIA_DIR}" \
    --disable-static \
    --enable-shared \
    --enable-nonfree \
    --enable-cuda-nvcc \
    --enable-libnpp \
    --extra-cflags="-I${CUDA_PATH}/include" \
    --extra-ldflags="-L${CUDA_PATH}/lib64" \
    --nvcc="$CUDA_PATH/bin/nvcc" \
    --nvccflags="-gencode arch=compute_86,code=sm_86" || error_exit "FFmpeg configuration failed"

# Build FFmpeg
echo -e "${YELLOW}Building FFmpeg...${NC}"
sudo -u $SUDO_USER make -j$(nproc) || error_exit "FFmpeg build failed"

# Install FFmpeg
echo -e "${YELLOW}Installing FFmpeg...${NC}"
make install || error_exit "FFmpeg installation failed"

echo -e "${GREEN}FFmpeg configuration, build, and installation completed successfully!${NC}"
echo "Build directory: $BUILD_DIR"
echo "You can check ffbuild/config.log for configuration details"

exit 0
