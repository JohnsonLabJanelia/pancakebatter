#!/bin/bash

# ANSI color codes
RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
BLUE="\033[0;34m"
NC="\033[0m"  # No Color

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root (sudo)${NC}"
    exit 1
fi

ORANGE_ROOT="/opt/orange"
NPROC=$(nproc)

# Check for CUDA installation
CUDA_PATH="/usr/local/cuda"
echo -e "${BLUE}Checking CUDA installation...${NC}"
if [ ! -d "$CUDA_PATH" ]; then
    echo -e "${RED}Error: CUDA installation not found at $CUDA_PATH${NC}"
    exit 1
fi

echo -e "${GREEN}Found CUDA installation at: $CUDA_PATH${NC}"
echo -e "${GREEN}CUDA version: $(basename $(readlink -f $CUDA_PATH))${NC}"

# Verify critical CUDA components
if [ ! -f "$CUDA_PATH/include/cuda.h" ]; then
    echo -e "${RED}Error: CUDA headers not found${NC}"
    exit 1
fi

if [ ! -d "$CUDA_PATH/lib64" ]; then
    echo -e "${RED}Error: CUDA libraries not found${NC}"
    exit 1
fi

# Set up NVIDIA codec headers first
echo -e "${BLUE}Setting up NVIDIA codec headers...${NC}"
if [ -d "$ORANGE_ROOT/lib/nv-codec-headers" ]; then
    cd $ORANGE_ROOT/lib/nv-codec-headers
    echo -e "${YELLOW}Installing NVIDIA codec headers...${NC}"
    make install
    if [ $? -ne 0 ]; then
        echo -e "${RED}Failed to install NVIDIA codec headers${NC}"
        exit 1
    fi
else
    echo -e "${RED}Error: NVIDIA codec headers not found in $ORANGE_ROOT/lib/nv-codec-headers${NC}"
    exit 1
fi

echo -e "${BLUE}Building FFmpeg...${NC}"
echo -e "${YELLOW}Building FFmpeg with NVIDIA support...${NC}"

# Fix FFmpeg permissions and build
cd $ORANGE_ROOT/lib/ffmpeg-nvidia
chmod -R 755 .

# Configure FFmpeg with updated NVIDIA support
PKG_CONFIG_PATH="/usr/local/lib/pkgconfig" ./configure \
    --prefix=$ORANGE_ROOT/lib/ffmpeg \
    --enable-shared \
    --enable-nonfree \
    --enable-cuda \
    --enable-cuvid \
    --enable-nvdec \
    --enable-nvenc \
    --enable-gpl \
    --extra-cflags="-I$CUDA_PATH/include -I/usr/local/include" \
    --extra-ldflags="-L$CUDA_PATH/lib64 -L/usr/local/lib" \
    --nvccflags="-gencode arch=compute_75,code=sm_75"

if [ $? -ne 0 ]; then
    echo -e "${RED}FFmpeg configure failed${NC}"
    echo -e "${YELLOW}Check ffbuild/config.log for more details${NC}"
    exit 1
fi

make -j$NPROC
if [ $? -ne 0 ]; then
    echo -e "${RED}FFmpeg make failed${NC}"
    exit 1
fi

make install
if [ $? -ne 0 ]; then
    echo -e "${RED}FFmpeg installation failed${NC}"
    exit 1
fi

echo -e "${BLUE}Building OpenCV...${NC}"

# Clean and prepare OpenCV build directory
rm -rf $ORANGE_ROOT/lib/opencv-4.8.0/build
mkdir -p $ORANGE_ROOT/lib/opencv-4.8.0/build
chmod -R 755 $ORANGE_ROOT/lib/opencv-4.8.0

cd $ORANGE_ROOT/lib/opencv-4.8.0/build

# Configure OpenCV with CUDA support
cmake .. \
    -DCMAKE_INSTALL_PREFIX=$ORANGE_ROOT/lib/opencv \
    -DOPENCV_EXTRA_MODULES_PATH=$ORANGE_ROOT/lib/opencv_contrib-4.8.0/modules \
    -DWITH_CUDA=ON \
    -DWITH_CUDNN=ON \
    -DOPENCV_DNN_CUDA=ON \
    -DWITH_CUBLAS=ON \
    -DWITH_FFMPEG=ON \
    -DWITH_GTK=ON \
    -DBUILD_TESTS=OFF \
    -DBUILD_PERF_TESTS=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DCUDA_TOOLKIT_ROOT_DIR=$CUDA_PATH \
    -DCMAKE_PREFIX_PATH="$ORANGE_ROOT/lib/ffmpeg"

if [ $? -ne 0 ]; then
    echo -e "${RED}OpenCV cmake configuration failed${NC}"
    exit 1
fi

make -j$NPROC
if [ $? -ne 0 ]; then
    echo -e "${RED}OpenCV make failed${NC}"
    exit 1
fi

make install
if [ $? -ne 0 ]; then
    echo -e "${RED}OpenCV installation failed${NC}"
    exit 1
fi

# Set up pkg-config files
echo -e "${BLUE}Setting up pkg-config entries...${NC}"
mkdir -p /usr/local/lib/pkgconfig

cat > /usr/local/lib/pkgconfig/orange-ffmpeg.pc << EOF
prefix=$ORANGE_ROOT/lib/ffmpeg
exec_prefix=\${prefix}
libdir=\${prefix}/lib
includedir=\${prefix}/include

Name: Orange FFmpeg
Description: FFmpeg with NVIDIA support for Orange project
Version: $(date +%Y%m%d)
Libs: -L\${libdir} -lavcodec -lavformat -lavutil -lswscale
Cflags: -I\${includedir}
EOF

cat > /usr/local/lib/pkgconfig/orange-opencv.pc << EOF
prefix=$ORANGE_ROOT/lib/opencv
exec_prefix=\${prefix}
libdir=\${prefix}/lib
includedir=\${prefix}/include/opencv4

Name: Orange OpenCV
Description: OpenCV with CUDA support for Orange project
Version: 4.8.0
Libs: -L\${libdir} -lopencv_core -lopencv_imgcodecs -lopencv_imgproc -lopencv_highgui -lopencv_videoio -lopencv_sfm
Cflags: -I\${includedir}
EOF

# Set up environment file
echo -e "${BLUE}Setting up environment file...${NC}"
cat > /etc/profile.d/orange.sh << EOF
# Environment settings for Orange project
export ORANGE_ROOT=$ORANGE_ROOT
export PATH=\$ORANGE_ROOT/lib/ffmpeg/bin:\$ORANGE_ROOT/lib/opencv/bin:\$PATH
export LD_LIBRARY_PATH=\$ORANGE_ROOT/lib/ffmpeg/lib:\$ORANGE_ROOT/lib/opencv/lib:\$LD_LIBRARY_PATH
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:\$PKG_CONFIG_PATH
EOF

echo -e "${GREEN}Build complete!${NC}"
echo -e "${YELLOW}Please run: source /etc/profile.d/orange.sh${NC}"
echo -e "${YELLOW}Then run the dependency checker to verify the installation${NC}"