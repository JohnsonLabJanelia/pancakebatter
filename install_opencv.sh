#!/bin/bash

# OpenCV Build Script with Custom Installation Directory
# Updated for Jeremy Delahanty, February 2025

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
OPENCV_VERSION="4.10.0"

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

# Check CUDA availability
echo -e "${BLUE}Checking CUDA installation...${NC}"
if ! command -v nvcc &> /dev/null; then
    echo -e "${YELLOW}CUDA toolkit not found! Disabling CUDA support...${NC}"
    CUDA_ENABLED=false
else
    CUDA_VERSION=$(nvcc --version | grep "release" | awk '{print $6}' | cut -c2-)
    echo -e "${GREEN}CUDA toolkit found (version $CUDA_VERSION). Enabling CUDA support...${NC}"
    CUDA_ENABLED=true
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
    libtbb-dev

# Create all required directories
echo -e "${BLUE}Creating directories...${NC}"
rm -rf "$BUILD_DIR"  # Clean up any existing directory
mkdir -p "$BUILD_DIR" || error_exit "Failed to create build directory"
mkdir -p "$OPENCV_BUILD" || error_exit "Failed to create OpenCV build directory"
mkdir -p "$OPENCV_CONTRIB_BUILD" || error_exit "Failed to create OpenCV contrib directory"
mkdir -p "$OPENCV_DIR" || error_exit "Failed to create installation directory"

# Set username and group for correct ownership
USER_NAME=${SUDO_USER:-$(logname)}
USER_GROUP=$(id -gn "$USER_NAME")

# Set ownership for build directory
chown -R "$USER_NAME":"$USER_GROUP" "$BUILD_DIR" || error_exit "Failed to change ownership of build directory"

# Download OpenCV and OpenCV contrib
echo -e "${BLUE}Downloading OpenCV ${OPENCV_VERSION}...${NC}"
cd "$BUILD_DIR" || error_exit "Failed to enter build directory"
sudo -u $SUDO_USER curl -sL "https://github.com/opencv/opencv/archive/${OPENCV_VERSION}.tar.gz" -o "opencv.tar.gz" || error_exit "Failed to download OpenCV"
sudo -u $SUDO_USER tar -xzf opencv.tar.gz || error_exit "Failed to extract OpenCV"
rm opencv.tar.gz
mv "opencv-${OPENCV_VERSION}" "$OPENCV_BUILD"

echo -e "${BLUE}Downloading OpenCV Contrib ${OPENCV_VERSION}...${NC}"
sudo -u $SUDO_USER curl -sL "https://github.com/opencv/opencv_contrib/archive/${OPENCV_VERSION}.tar.gz" -o "opencv_contrib.tar.gz" || error_exit "Failed to download OpenCV Contrib"
sudo -u $SUDO_USER tar -xzf opencv_contrib.tar.gz || error_exit "Failed to extract OpenCV Contrib"
rm opencv_contrib.tar.gz
mv "opencv_contrib-${OPENCV_VERSION}" "$OPENCV_CONTRIB_BUILD"

# Configure and build OpenCV
echo -e "${YELLOW}Configuring OpenCV...${NC}"
cd "$OPENCV_BUILD/opencv-${OPENCV_VERSION}" || error_exit "Failed to change to OpenCV source directory"
mkdir -p build
cd build

# Create build directory with proper permissions
chown -R $USER_NAME:$USER_GROUP ../build

# Set CUDA options based on availability
if [ "$CUDA_ENABLED" = true ]; then
    # Get CUDA compute capability
    CUDA_ARCH="8.6"  # Default for your A6000 and A16
    
    # Check if CUDNN is installed
    if [ -f "/usr/local/cuda/include/cudnn.h" ]; then
        echo -e "${GREEN}cuDNN found. Enabling cuDNN support...${NC}"
        CUDNN_OPTIONS="-D WITH_CUDNN=ON -D OPENCV_DNN_CUDA=ON"
    else
        echo -e "${YELLOW}cuDNN not found. Disabling cuDNN support...${NC}"
        CUDNN_OPTIONS="-D WITH_CUDNN=OFF -D OPENCV_DNN_CUDA=OFF"
    fi
    
    CUDA_OPTIONS="-D WITH_CUDA=ON \
    -D CUDA_FAST_MATH=1 \
    -D WITH_CUBLAS=1 \
    -D CUDA_ARCH_BIN=$CUDA_ARCH \
    -D BUILD_opencv_cudacodec=ON \
    $CUDNN_OPTIONS"
else
    CUDA_OPTIONS="-D WITH_CUDA=OFF -D OPENCV_DNN_CUDA=OFF"
fi

# Use GCC 12 for CUDA builds, as of 07162025, GCC>12 not supported for NVIDIA CUDA Kernels
if [ -x /usr/bin/gcc-12 ]; then
    export CC=/usr/bin/gcc-12
    export CXX=/usr/bin/g++-12
    HOST_COMPILER_OPTION="-D CUDA_HOST_COMPILER=/usr/bin/gcc-12"
else
    echo -e "${YELLOW}GCC 12 not found, CUDA build may fail. Run: sudo apt install gcc-12 g++-12${NC}"
    HOST_COMPILER_OPTION=""
fi

# Configure with CMake as the regular user
echo -e "${YELLOW}Running CMake...${NC}"
sudo -u $SUDO_USER cmake \
    -D CMAKE_BUILD_TYPE=RELEASE \
    -D CMAKE_INSTALL_PREFIX="$OPENCV_DIR" \
    -D OPENCV_PC_FILE_NAME=opencv4.pc \
    -D INSTALL_PYTHON_EXAMPLES=OFF \
    -D INSTALL_C_EXAMPLES=ON \
    -D WITH_TBB=ON \
    -D WITH_V4L=ON \
    -D WITH_QT=ON \
    -D WITH_GTK=ON \
    -D WITH_OPENGL=ON \
    -D WITH_GTK_2_X=OFF \
    -D WITH_GSTREAMER=ON \
    -D WITH_FFMPEG=ON \
    -D ENABLE_FAST_MATH=1 \
    -D OPENCV_ENABLE_NONFREE=ON \
    -D OPENCV_GENERATE_PKGCONFIG=ON \
    -D OPENCV_EXTRA_MODULES_PATH="$OPENCV_CONTRIB_BUILD/opencv_contrib-${OPENCV_VERSION}/modules" \
    -D BUILD_EXAMPLES=ON \
    $CUDA_OPTIONS \
    $HOST_COMPILER_OPTION \
    .. || error_exit "CMake configuration failed"

# Build OpenCV
echo -e "${YELLOW}Building OpenCV...${NC}"
sudo -u $SUDO_USER make -j$(nproc) || error_exit "OpenCV compilation failed"

# Install OpenCV
echo -e "${YELLOW}Installing OpenCV...${NC}"
make install || error_exit "OpenCV installation failed"

# Set up environment variables
echo -e "${BLUE}Setting up environment variables...${NC}"
cat > /etc/profile.d/opencv-orange.sh << EOF
# OpenCV configuration for Orange project
export OPENCV_HOME=${OPENCV_DIR}
export PATH=\$OPENCV_HOME/bin:\$PATH
export LD_LIBRARY_PATH=\$OPENCV_HOME/lib:\$LD_LIBRARY_PATH
export PKG_CONFIG_PATH=\$OPENCV_HOME/lib/pkgconfig:\$PKG_CONFIG_PATH
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
if [ "$CUDA_ENABLED" = true ]; then
    echo "CUDA Support: Enabled (Compute Capability: $CUDA_ARCH)"
else
    echo "CUDA Support: Disabled"
fi

exit 0
