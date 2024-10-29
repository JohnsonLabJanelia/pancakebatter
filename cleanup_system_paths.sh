#!/bin/bash

# Clean up any existing related variables
if [ -n "$LD_LIBRARY_PATH" ]; then
    LD_LIBRARY_PATH=$(echo $LD_LIBRARY_PATH | tr ':' '\n' | sort -u | grep -v "ffmpeg/lib" | tr '\n' ':')
fi

if [ -n "$PKG_CONFIG_PATH" ]; then
    PKG_CONFIG_PATH=$(echo $PKG_CONFIG_PATH | tr ':' '\n' | sort -u | tr '\n' ':')
fi

if [ -n "$PATH" ]; then
    PATH=$(echo $PATH | tr ':' '\n' | sort -u | tr '\n' ':')
fi

# Environment settings for Orange project
export ORANGE_ROOT=/opt/orange

# Set up clean paths
export LD_LIBRARY_PATH="\
$ORANGE_ROOT/lib/ffmpeg-nvidia/lib:\
$ORANGE_ROOT/lib/opencv/lib:\
/usr/local/cuda/lib64:\
/usr/local/TensorRT-10.0.1.6/lib\
${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

export PKG_CONFIG_PATH="\
$ORANGE_ROOT/lib/ffmpeg-nvidia/lib/pkgconfig:\
$ORANGE_ROOT/lib/opencv/lib/pkgconfig:\
/usr/local/lib/pkgconfig\
${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"

export PATH="\
$ORANGE_ROOT/lib/ffmpeg-nvidia/bin:\
$ORANGE_ROOT/lib/opencv/bin\
${PATH:+:$PATH}"

# Force ldconfig to update cache
sudo ldconfig