#!/bin/bash

# ANSI color codes for formatting output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color (Reset)

# Orange ecosystem paths
ORANGE_ROOT="/opt/orange"
TENSORRT_OSS_DIR="${ORANGE_ROOT}/lib/tensorrt-oss"
SOURCE_DIR="${TENSORRT_OSS_DIR}/source"
BUILD_DIR="${TENSORRT_OSS_DIR}/build"
PLUGINS_DIR="${TENSORRT_OSS_DIR}/plugins"

# System TensorRT installation
SYSTEM_TRT_DIR="/usr/local/TensorRT-10.0.1.6"
SYSTEM_TRT_LIB="${SYSTEM_TRT_DIR}/lib"

echo ""
echo -e "${BLUE}TensorRT OSS EfficientNMS Plugin Installation${NC}"
echo "Installing in: ${ORANGE_ROOT}"
echo ""

# Check if running as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}ERROR: Please run this script as root or with sudo.${NC}"
    echo ""
    exit 1
fi

# Function to check dependencies
check_dependencies() {
    echo -e "${YELLOW}Checking dependencies...${NC}"
    local missing_deps=0
    
    # Check CUDA
    if ! command -v nvcc &> /dev/null; then
        echo -e "${RED}✗ CUDA toolkit not found${NC}"
        missing_deps=1
    else
        local cuda_version=$(nvcc --version | grep "release" | awk '{print $6}' | cut -c2-)
        echo -e "${GREEN}✓ CUDA ${cuda_version} found${NC}"
    fi
    
    # Check TensorRT
    if [ ! -d "$SYSTEM_TRT_DIR" ]; then
        echo -e "${RED}✗ TensorRT installation not found at ${SYSTEM_TRT_DIR}${NC}"
        missing_deps=1
    else
        echo -e "${GREEN}✓ TensorRT found at ${SYSTEM_TRT_DIR}${NC}"
    fi
    
    # Check build tools
    local tools=("cmake" "make" "git")
    for tool in "${tools[@]}"; do
        if ! command -v "$tool" &> /dev/null; then
            echo -e "${RED}✗ ${tool} not found${NC}"
            missing_deps=1
        else
            echo -e "${GREEN}✓ ${tool} found${NC}"
        fi
    done
    
    if [ $missing_deps -eq 1 ]; then
        echo -e "${RED}Missing dependencies. Please install required packages.${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}All dependencies satisfied.${NC}"
    echo ""
}

# Function to create directory structure
setup_directories() {
    echo -e "${YELLOW}Setting up directory structure...${NC}"
    
    # Create Orange lib structure if it doesn't exist
    mkdir -p "${ORANGE_ROOT}/lib" || {
        echo -e "${RED}Failed to create Orange lib directory${NC}"
        exit 1
    }
    
    # Create TensorRT OSS directories
    mkdir -p "${SOURCE_DIR}" "${BUILD_DIR}" "${PLUGINS_DIR}" || {
        echo -e "${RED}Failed to create TensorRT OSS directories${NC}"
        exit 1
    }
    
    # Set ownership to the user who invoked sudo
    local real_user=${SUDO_USER:-$(logname)}
    chown -R "${real_user}:${real_user}" "${TENSORRT_OSS_DIR}" || {
        echo -e "${YELLOW}Warning: Could not set ownership for ${real_user}${NC}"
    }
    
    echo -e "${GREEN}✓ Directory structure created${NC}"
    echo "  Source: ${SOURCE_DIR}"
    echo "  Build:  ${BUILD_DIR}"
    echo "  Output: ${PLUGINS_DIR}"
    echo ""
}

# Function to clone TensorRT OSS
clone_tensorrt_oss() {
    echo -e "${YELLOW}Getting TensorRT OSS source code...${NC}"
    
    cd "${SOURCE_DIR}" || exit 1
    
    if [ -d ".git" ]; then
        echo -e "${BLUE}TensorRT OSS already cloned, updating...${NC}"
        sudo -u "${SUDO_USER}" git pull
    else
        echo -e "${BLUE}Cloning TensorRT OSS release/10.0...${NC}"
        sudo -u "${SUDO_USER}" git clone -b release/10.0 https://github.com/NVIDIA/TensorRT.git . || {
            echo -e "${RED}Failed to clone TensorRT OSS${NC}"
            exit 1
        }
    fi
    
    echo -e "${BLUE}Updating submodules...${NC}"
    sudo -u "${SUDO_USER}" git submodule update --init --recursive || {
        echo -e "${RED}Failed to update submodules${NC}"
        exit 1
    }
    
    echo -e "${GREEN}✓ TensorRT OSS source ready${NC}"
    echo ""
}

# Function to configure and build
build_plugins() {
    echo -e "${YELLOW}Configuring TensorRT OSS build...${NC}"
    
    cd "${BUILD_DIR}" || exit 1
    
    # Clean previous build
    rm -rf ./*
    
    # Get CUDA version for build
    local cuda_version=$(nvcc --version | grep "release" | awk '{print $6}' | cut -c2-)
    echo -e "${BLUE}Building for CUDA ${cuda_version}${NC}"
    
    # Configure with CMake
    sudo -u "${SUDO_USER}" cmake "${SOURCE_DIR}" \
        -DTRT_LIB_DIR="${SYSTEM_TRT_LIB}" \
        -DTRT_OUT_DIR="${PLUGINS_DIR}" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCUDA_VERSION="${cuda_version}" \
        -DCMAKE_CUDA_ARCHITECTURES="80;86" \
        -DBUILD_PLUGINS=ON || {
        echo -e "${RED}CMake configuration failed${NC}"
        exit 1
    }
    
    echo -e "${GREEN}✓ Configuration successful${NC}"
    echo ""
    
    echo -e "${YELLOW}Building TensorRT OSS plugins...${NC}"
    echo "This may take several minutes..."
    
    sudo -u "${SUDO_USER}" make -j$(nproc) || {
        echo -e "${RED}Build failed${NC}"
        exit 1
    }
    
    echo -e "${GREEN}✓ Build completed successfully${NC}"
    echo ""
}

# Function to verify plugins
verify_plugins() {
    echo -e "${YELLOW}Verifying EfficientNMS plugins...${NC}"
    
    local plugin_lib="${PLUGINS_DIR}/libnvinfer_plugin.so"
    
    if [ ! -f "$plugin_lib" ]; then
        echo -e "${RED}✗ Plugin library not found at ${plugin_lib}${NC}"
        exit 1
    fi
    
    echo -e "${GREEN}✓ Plugin library found${NC}"
    
    # Check for EfficientNMS symbols
    local efficient_symbols=$(nm -D "$plugin_lib" | grep -i efficient | wc -l)
    if [ "$efficient_symbols" -gt 0 ]; then
        echo -e "${GREEN}✓ Found ${efficient_symbols} EfficientNMS symbols${NC}"
    else
        echo -e "${RED}✗ No EfficientNMS symbols found${NC}"
        exit 1
    fi
    
    # Create Python verification script
    cat > /tmp/verify_tensorrt_plugins.py << 'EOF'
import sys
import os
import ctypes

# Add the plugin library path
plugin_lib = sys.argv[1] if len(sys.argv) > 1 else "/opt/orange/lib/tensorrt-oss/plugins/libnvinfer_plugin.so"

try:
    import tensorrt as trt
    
    # Load custom plugins
    if os.path.exists(plugin_lib):
        print(f"Loading plugin library: {plugin_lib}")
        ctypes.CDLL(plugin_lib, mode=ctypes.RTLD_GLOBAL)
    else:
        print(f"Plugin library not found: {plugin_lib}")
        sys.exit(1)
    
    # Initialize plugins
    TRT_LOGGER = trt.Logger(trt.Logger.WARNING)
    trt.init_libnvinfer_plugins(TRT_LOGGER, "")
    
    # Check for EfficientNMS plugins
    registry = trt.get_plugin_registry()
    efficient_plugins = []
    
    for creator in registry.plugin_creator_list:
        if "EfficientNMS" in creator.name:
            efficient_plugins.append(f"{creator.name} (v{creator.plugin_version})")
    
    if efficient_plugins:
        print("✅ EfficientNMS plugins found:")
        for plugin in efficient_plugins:
            print(f"  - {plugin}")
        sys.exit(0)
    else:
        print("❌ No EfficientNMS plugins found")
        sys.exit(1)
        
except ImportError as e:
    print(f"❌ TensorRT Python bindings not found: {e}")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error during verification: {e}")
    sys.exit(1)
EOF
    
    # Run verification
    if python3 /tmp/verify_tensorrt_plugins.py "$plugin_lib"; then
        echo -e "${GREEN}✓ Plugin verification successful${NC}"
    else
        echo -e "${RED}✗ Plugin verification failed${NC}"
        exit 1
    fi
    
    # Clean up
    rm -f /tmp/verify_tensorrt_plugins.py
    echo ""
}

# Function to install system-wide (optional)
install_system_wide() {
    echo -e "${YELLOW}Would you like to install plugins system-wide?${NC}"
    echo "This will copy the plugin library to your TensorRT installation."
    read -p "Install system-wide? (y/n): " -n 1 -r
    echo
    
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${BLUE}Installing plugins system-wide...${NC}"
        
        local plugin_src="${PLUGINS_DIR}/libnvinfer_plugin.so.10.0.1"
        local plugin_dst="${SYSTEM_TRT_LIB}/libnvinfer_plugin.so.10.0.1"
        
        if [ -f "$plugin_src" ]; then
            cp "$plugin_src" "$plugin_dst" || {
                echo -e "${RED}Failed to copy plugin library${NC}"
                exit 1
            }
            
            # Update library cache
            ldconfig
            
            echo -e "${GREEN}✓ Plugins installed system-wide${NC}"
        else
            echo -e "${RED}✗ Plugin library not found at ${plugin_src}${NC}"
            exit 1
        fi
    else
        echo -e "${YELLOW}Skipping system-wide installation${NC}"
        echo "Plugins available at: ${PLUGINS_DIR}"
    fi
    echo ""
}

# Function to create environment setup
create_environment() {
    echo -e "${YELLOW}Creating environment configuration...${NC}"
    
    cat > /etc/profile.d/tensorrt-oss.sh << EOF
# TensorRT OSS Environment for Orange Project
export TENSORRT_OSS_ROOT="${TENSORRT_OSS_DIR}"
export TENSORRT_OSS_PLUGINS="${PLUGINS_DIR}"
export LD_LIBRARY_PATH="\${TENSORRT_OSS_PLUGINS}:\$LD_LIBRARY_PATH"
EOF
    
    chmod 644 /etc/profile.d/tensorrt-oss.sh
    
    echo -e "${GREEN}✓ Environment configuration created${NC}"
    echo "File: /etc/profile.d/tensorrt-oss.sh"
    echo ""
}

# Main execution
main() {
    check_dependencies
    setup_directories
    clone_tensorrt_oss
    build_plugins
    verify_plugins
    install_system_wide
    create_environment
    
    echo -e "${GREEN}=== TensorRT OSS Installation Complete ===${NC}"
    echo ""
    echo "Plugin library location: ${PLUGINS_DIR}/libnvinfer_plugin.so"
    echo "To use in applications:"
    echo "  export LD_LIBRARY_PATH=\"${PLUGINS_DIR}:\$LD_LIBRARY_PATH\""
    echo "  python3 your_app.py"
    echo ""
    echo "Or source the environment:"
    echo "  source /etc/profile.d/tensorrt-oss.sh"
    echo ""
    echo -e "${YELLOW}Please log out and log back in for environment variables to take effect.${NC}"
}

# Run main function
main "$@"