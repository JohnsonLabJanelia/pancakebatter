#!/bin/bash

# ANSI color codes for output
RED="\033[0;31m"  # Red color for errors
GREEN="\033[0;32m"  # Green color for success
YELLOW="\033[0;33m"  # Yellow color for warnings
BLUE="\033[0;34m"  # Blue color for informational messages
NC="\033[0m"  # No Color (reset to default)

# Expected paths for different environment variables
EXPECTED_PATHS=(
    "/opt/orange/lib/ffmpeg-nvidia/bin"  # FFmpeg binaries
    "/opt/orange/lib/opencv/bin"  # OpenCV binaries
    "/usr/local/cuda/bin"  # CUDA binaries
    "/usr/local/sbin"  # System administration binaries
    "/usr/local/bin"  # Local user binaries
    "/usr/sbin"  # System binaries
    "/usr/bin"  # Standard user binaries
    "/sbin"  # System administration binaries
    "/bin"  # Essential command binaries
)

EXPECTED_LD_LIBRARY_PATHS=(
    "/opt/orange/lib/ffmpeg-nvidia/lib"  # FFmpeg libraries
    "/opt/orange/lib/opencv/lib"  # OpenCV libraries
    "/usr/local/cuda/lib64"  # CUDA 64-bit libraries
    "/usr/local/TensorRT-10.0.1.6/lib"  # TensorRT libraries
)

EXPECTED_PKG_CONFIG_PATHS=(
    "/opt/orange/lib/ffmpeg-nvidia/lib/pkgconfig"  # FFmpeg pkg-config files
    "/opt/orange/lib/opencv/lib/pkgconfig"  # OpenCV pkg-config files
    "/usr/local/lib/pkgconfig"  # General pkg-config files
)

# Function to check if all expected paths are in the given variable
# Arguments:
#   1. variable_name: The name of the environment variable being checked (e.g., PATH)
#   2. expected_paths: Array of expected paths to check against
#   3. actual_paths: The current value of the environment variable
check_paths() {
    local variable_name="$1"  # Name of the environment variable
    local expected_paths=("${!2}")  # Array of expected paths
    local actual_paths="$3"  # Current value of the environment variable
    local errors=0  # Counter for missing paths

    # Loop through each expected path and check if it is present in the actual paths
    for path in "${expected_paths[@]}"; do
        if [[ ":$actual_paths:" != *":$path:"* ]]; then
            # Path is missing, print error message
            echo -e "${RED}✗ Missing expected path in $variable_name: $path${NC}"
            ((errors++))
        else
            # Path is found, print success message
            echo -e "${GREEN}✓ Path found in $variable_name: $path${NC}"
        fi
    done

    return $errors  # Return the number of missing paths
}

# Main script execution
# Checking if the expected paths are present in PATH, LD_LIBRARY_PATH, and PKG_CONFIG_PATH
echo -e "${BLUE}Checking PATH...${NC}"
check_paths "PATH" EXPECTED_PATHS[@] "$PATH"
path_errors=$?  # Store the number of errors found in PATH

echo -e "\n${BLUE}Checking LD_LIBRARY_PATH...${NC}"
check_paths "LD_LIBRARY_PATH" EXPECTED_LD_LIBRARY_PATHS[@] "$LD_LIBRARY_PATH"
ld_library_path_errors=$?  # Store the number of errors found in LD_LIBRARY_PATH

echo -e "\n${BLUE}Checking PKG_CONFIG_PATH...${NC}"
check_paths "PKG_CONFIG_PATH" EXPECTED_PKG_CONFIG_PATHS[@] "$PKG_CONFIG_PATH"
pkg_config_path_errors=$?  # Store the number of errors found in PKG_CONFIG_PATH

# Summary of the checks
if (( path_errors == 0 && ld_library_path_errors == 0 && pkg_config_path_errors == 0 )); then
    # If no errors were found, all paths are set correctly
    echo -e "\n${GREEN}✓ All paths are set correctly!${NC}"
    exit 0  # Exit with success code
else
    # If there are errors, print a summary message
    echo -e "\n${RED}✗ Some paths are missing or incorrect. Please review the output above.${NC}"
    exit 1  # Exit with error code
fi
