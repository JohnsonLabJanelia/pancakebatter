#!/bin/bash

# OpenCV Build Script with Custom Installation Directory
# Updated by Claude for Jeremy Delahanty, October 2024

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Build directories (temporary)
BUILD_DIR="/tmp/opencv-build-$$"
OPENCV_BUILD="${BUILD_DIR}/opencv"
OPENCV_CONTRIB_BUILD="${BUILD_DIR}/opencv_contrib"

# Final installation directories
INSTALL_DIR="/opt/orange"
OPENCV_DIR="${INSTALL_DIR}/lib/opencv"

# OpenCV version
OPENCV_VERSION="4.8.0"

# Function to exit with an error message
error_exit() {
    echo -e "${RED}Error: $1${NC}" >&2
    if [ -d "$BUILD_DIR" ]; then
        echo -e "${YELLOW}Build directory preserved at: $BUILD_DIR${NC}"
    fi
    exit 1
}

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    error_exit "This script must be run as root. Please use sudo."
fi

# Install required packages
echo -e "${YELLOW}Installing required packages...${NC}"
apt-get update
apt-get install -y build-essential cmake pkg-config \
    libjpeg-dev libpng-dev libtiff-dev \
    libavcodec-dev libavformat-dev libswscale-dev \
    libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev \
    libxvidcore-dev x264 libx264-dev libfaac-dev libmp3lame-dev libtheora-dev \
    libfaac-dev libmp3lame-dev libvorbis-dev \
    libopencore-amrnb-dev libopencore-amrwb-dev \
    libatlas-base-dev gfortran libeigen3-dev \
    python3-dev python3-numpy python3-pip \
    libtbb2 libtbb-dev

# Create all required directories
echo -e "${BLUE}Creating directories...${NC}"
rm -rf "$BUILD_DIR"  # Clean up any existing directory
mkdir -p "$BUILD_DIR" || error_exit "Failed to create build directory"
mkdir -p "$OPENCV_BUILD" || error_exit "Failed to create OpenCV build directory"
mkdir -p "$OPENCV_CONTRIB_BUILD" || error_exit "Failed to create OpenCV contrib directory"
mkdir -p "$OPENCV_DIR" || error_exit "Failed to create installation directory"

# Set ownership for build directory
chown -R $SUDO_USER:$SUDO_USER "$BUILD_DIR" || error_exit "Failed to change ownership of build directory"

# Download OpenCV and OpenCV contrib
echo -e "${BLUE}Downloading OpenCV ${OPENCV_VERSION}...${NC}"
cd "$OPENCV_BUILD" || error_exit "Failed to change to OpenCV directory"
sudo -u $SUDO_USER wget -q "https://github.com/opencv/opencv/archive/${OPENCV_VERSION}.zip" || error_exit "Failed to download OpenCV"
sudo -u $SUDO_USER unzip -q "${OPENCV_VERSION}.zip" || error_exit "Failed to unzip OpenCV"
rm "${OPENCV_VERSION}.zip"

echo -e "${BLUE}Downloading OpenCV Contrib ${OPENCV_VERSION}...${NC}"
cd "$OPENCV_CONTRIB_BUILD" || error_exit "Failed to change to OpenCV Contrib directory"
sudo -u $SUDO_USER wget -q "https://github.com/opencv/opencv_contrib/archive/${OPENCV_VERSION}.zip" || error_exit "Failed to download OpenCV Contrib"
sudo -u $SUDO_USER unzip -q "${OPENCV_VERSION}.zip" || error_exit "Failed to unzip OpenCV Contrib"
rm "${OPENCV_VERSION}.zip"

# Configure and build OpenCV
echo -e "${YELLOW}Configuring OpenCV...${NC}"
cd "$OPENCV_BUILD/opencv-${OPENCV_VERSION}" || error_exit "Failed to change to OpenCV source directory"
mkdir -p build
cd build

# Create build directory with proper permissions
chown -R $SUDO_USER:$SUDO_USER ../build

# Configure with CMake as the regular user
echo -e "${YELLOW}Running CMake...${NC}"
sudo -u $SUDO_USER cmake \
    -D CMAKE_BUILD_TYPE=RELEASE \
    -D CMAKE_INSTALL_PREFIX="$OPENCV_DIR" \
    -D INSTALL_PYTHON_EXAMPLES=OFF \
    -D INSTALL_C_EXAMPLES=OFF \
    -D WITH_TBB=ON \
    -D WITH_V4L=ON \
    -D WITH_OPENGL=ON \
    -D WITH_CUDA=OFF \
    -D BUILD_opencv_cudacodec=OFF \
    -D ENABLE_FAST_MATH=1 \
    -D OPENCV_ENABLE_NONFREE=ON \
    -D OPENCV_GENERATE_PKGCONFIG=ON \
    -D OPENCV_EXTRA_MODULES_PATH="$OPENCV_CONTRIB_BUILD/opencv_contrib-${OPENCV_VERSION}/modules" \
    -D BUILD_EXAMPLES=OFF .. || error_exit "CMake configuration failed"

# Build OpenCV
echo -e "${YELLOW}Building OpenCV...${NC}"
sudo -u $SUDO_USER make -j$(nproc) || error_exit "OpenCV compilation failed"

# Install OpenCV
echo -e "${YELLOW}Installing OpenCV...${NC}"
make install || error_exit "OpenCV installation failed"

# Set up environment variables
echo -e "${BLUE}Setting up environment variables...${NC}"
cat > /etc/profile.d/opencv-orange.sh << 'EOF'
# OpenCV configuration for Orange project
export OPENCV_HOME=${OPENCV_DIR}
export PATH=$OPENCV_HOME/bin:$PATH
export LD_LIBRARY_PATH=$OPENCV_HOME/lib:$LD_LIBRARY_PATH
export PKG_CONFIG_PATH=$OPENCV_HOME/lib/pkgconfig:$PKG_CONFIG_PATH
EOF

# Update library cache
ldconfig

# Set permissions
chmod 644 /etc/profile.d/opencv-orange.sh
chown -R root:root "$INSTALL_DIR"
chmod -R 755 "$INSTALL_DIR"

# Clean up build directory
echo -e "${BLUE}Cleaning up...${NC}"
rm -rf "$BUILD_DIR"

echo -e "${GREEN}OpenCV installation completed successfully!${NC}"
echo "OpenCV installed in: ${OPENCV_DIR}"
echo "Environment configured in: /etc/profile.d/opencv-orange.sh"
echo "Please log out and log back in for environment variables to take effect"

# Print verification information
echo -e "\n${BLUE}Verification Information:${NC}"
echo "OpenCV Version: $(pkg-config --modversion opencv4)"
echo "Installation Location: ${OPENCV_DIR}"
echo "Environment File: /etc/profile.d/opencv-orange.sh"

exit 0