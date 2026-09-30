#!/bin/bash

# FFmpeg Build Script with CUDA and NVENC Support
# Updated by Claude for Jeremy Delahanty, October 2024

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
BUILD_DIR="/tmp/ffmpeg-build-$$"
FFMPEG_BUILD="${BUILD_DIR}/ffmpeg"
NVCODEC_BUILD="${BUILD_DIR}/nv-codec-headers"

# Final installation directories
INSTALL_DIR="/opt/orange"
NVIDIA_DIR="${INSTALL_DIR}/lib/ffmpeg-nvidia"

# Set up CUDA environment
CUDA_PATH="/usr/local/cuda"
export PATH="$CUDA_PATH/bin:$PATH"
export LD_LIBRARY_PATH="$CUDA_PATH/lib64:$LD_LIBRARY_PATH"
export CUDA_HOME="$CUDA_PATH"
export PKG_CONFIG_PATH="$CUDA_PATH/lib64/pkgconfig:/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"

# Function to exit with an error message
error_exit() {
    echo -e "${RED}Error: $1${NC}" >&2
    echo -e "${YELLOW}Build directory preserved at: $BUILD_DIR${NC}"
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error_exit "This script must be run as root. Please use sudo."
fi

# Create all required directories first
echo -e "${BLUE}Creating directories...${NC}"
mkdir -p "$BUILD_DIR" || error_exit "Failed to create build directory"
mkdir -p "$FFMPEG_BUILD" || error_exit "Failed to create FFmpeg build directory"
mkdir -p "$NVCODEC_BUILD" || error_exit "Failed to create NVCODEC build directory"
mkdir -p "$INSTALL_DIR/lib" || error_exit "Failed to create installation directory"

# Get username and usergroup
USER_NAME=${SUDO_USER:-$(logname)}
USER_GROUP=$(id -gn "$USER_NAME")

# Set correct ownership
chown -R "$USER_NAME":"$USER_GROUP" "$BUILD_DIR" || error_exit "Failed to change ownership of build directory"

# Install required packages
echo -e "${YELLOW}Installing required packages...${NC}"
install_apt_manifest_packages "FFmpeg CUDA build dependencies" ffmpeg_cuda \
    || error_exit "Failed to install required apt packages"

# Verify NVENC libraries
echo -e "${BLUE}Checking for NVIDIA libraries...${NC}"
cd "$NVCODEC_BUILD" || error_exit "Failed to change to NVCODEC directory"
rm -rf *  # Clean directory
sudo -u $SUDO_USER git clone https://git.videolan.org/git/ffmpeg/nv-codec-headers.git . || error_exit "Failed to clone nv-codec-headers"
sudo -u $SUDO_USER git checkout n11.1.5.2 || error_exit "Failed to checkout stable version"
sudo -u $SUDO_USER make || error_exit "Failed to build nv-codec-headers"
make install || error_exit "Failed to install nv-codec-headers"

# Update library cache
ldconfig

# Verify CUDA compiler
echo -e "${BLUE}Verifying CUDA compiler...${NC}"
if ! command -v nvcc &> /dev/null; then
    error_exit "nvcc not found or not working"
fi

NVCC_PATH=$(which nvcc)
echo "Found nvcc at: $NVCC_PATH"

# Build FFmpeg
echo -e "${YELLOW}Building FFmpeg...${NC}"
cd "$FFMPEG_BUILD" || error_exit "Failed to change to FFmpeg directory"
sudo -u $SUDO_USER git clone https://git.ffmpeg.org/ffmpeg.git . || error_exit "Failed to clone FFmpeg"
sudo -u $SUDO_USER git checkout release/4.4 || error_exit "Failed to checkout release 4.4"

# Configure FFmpeg with support for both A16 and A6000
echo -e "${YELLOW}Configuring FFmpeg...${NC}"
sudo -u $SUDO_USER PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH" ./configure \
    --prefix="$NVIDIA_DIR" \
    --disable-static \
    --enable-shared \
    --enable-nonfree \
    --enable-cuda-nvcc \
    --enable-libnpp \
    --enable-nvenc \
    --enable-nvdec \
    --enable-cuvid \
    --extra-cflags="-I$CUDA_PATH/include -I/usr/local/include" \
    --extra-ldflags="-L$CUDA_PATH/lib64 -L/usr/local/lib" \
    --nvcc="$NVCC_PATH" \
    --nvccflags="-gencode arch=compute_86,code=sm_86" \
    || error_exit "FFmpeg configuration failed"

# Build FFmpeg
echo -e "${YELLOW}Building FFmpeg...${NC}"
sudo -u $SUDO_USER make -j$(nproc) || error_exit "FFmpeg compilation failed"

# Install FFmpeg
echo -e "${YELLOW}Installing FFmpeg...${NC}"
make install || error_exit "FFmpeg installation failed"

# Update library cache again
ldconfig

# Set up environment variables
echo -e "${BLUE}Setting up environment variables...${NC}"
cat > /etc/profile.d/ffmpeg-orange.sh << EOF
# FFmpeg configuration for Orange project
export FFMPEG_HOME=${NVIDIA_DIR}
export CUDA_HOME=${CUDA_PATH}
export PATH=\$FFMPEG_HOME/bin:\$CUDA_HOME/bin:\$PATH
export LD_LIBRARY_PATH=\$FFMPEG_HOME/lib:\$CUDA_HOME/lib64:\$LD_LIBRARY_PATH
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:\$CUDA_HOME/lib64/pkgconfig:\$PKG_CONFIG_PATH
EOF

# Set permissions
chmod 644 /etc/profile.d/ffmpeg-orange.sh
chown -R root:root "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"

echo -e "${GREEN}FFmpeg installation completed successfully!${NC}"
echo "FFmpeg installed in: ${NVIDIA_DIR}"
echo "CUDA environment configured in: /etc/profile.d/ffmpeg-orange.sh"
echo "Please log out and log back in for environment variables to take effect"

# Print verification information
echo -e "\n${BLUE}Verification Information:${NC}"
echo "NVIDIA Driver Version: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)"
echo "CUDA Version: $(nvcc --version | grep release | awk '{print $5}')"
echo "FFmpeg Version: $($NVIDIA_DIR/bin/ffmpeg -version | head -n1)"
echo -e "\nTo verify NVENC support, please run: $NVIDIA_DIR/bin/ffmpeg -encoders | grep nvenc"

exit 0
