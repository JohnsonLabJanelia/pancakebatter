#!/bin/bash

# ANSI color codes
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}Cleaning up system paths...${NC}"

# Environment settings for Orange project
export ORANGE_ROOT=/opt/orange

# Clean up PATH
export PATH=$(echo $PATH | tr ':' '\n' | grep -v "ffmpeg-nvidia" | grep -v "opencv" | \
             grep -v "cuda-12.2" | grep -v "TensorRT-10.0.1.6" | sort -u | tr '\n' ':')

# Clean up LD_LIBRARY_PATH  
export LD_LIBRARY_PATH=$(echo $LD_LIBRARY_PATH | tr ':' '\n' | grep -v "ffmpeg-nvidia" | \
                         grep -v "opencv" | grep -v "cuda-12.2" | grep -v "TensorRT-10.0.1.6" | sort -u | tr '\n' ':')

# Clean up PKG_CONFIG_PATH
export PKG_CONFIG_PATH=$(echo $PKG_CONFIG_PATH | tr ':' '\n' | grep -v "ffmpeg-nvidia" | \
                         grep -v "opencv" | sort -u | tr '\n' ':')

# Remove trailing colon if present
PATH=${PATH%:}
LD_LIBRARY_PATH=${LD_LIBRARY_PATH%:}
PKG_CONFIG_PATH=${PKG_CONFIG_PATH%:}

# Add proper paths
export PATH="$ORANGE_ROOT/lib/ffmpeg-nvidia/bin:$ORANGE_ROOT/lib/opencv/bin:/usr/local/cuda/bin:$PATH"
export LD_LIBRARY_PATH="$ORANGE_ROOT/lib/ffmpeg-nvidia/lib:$ORANGE_ROOT/lib/opencv/lib:/usr/local/cuda/lib64:/usr/local/TensorRT-10.0.1.6/lib:$LD_LIBRARY_PATH"
export PKG_CONFIG_PATH="$ORANGE_ROOT/lib/ffmpeg-nvidia/lib/pkgconfig:$ORANGE_ROOT/lib/opencv/lib/pkgconfig:/usr/local/lib/pkgconfig:$PKG_CONFIG_PATH"

# Force ldconfig to update cache
sudo ldconfig

echo -e "${GREEN}Paths cleaned successfully!${NC}"