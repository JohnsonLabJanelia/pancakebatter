#!/bin/bash

# ANSI color codes
RED="\033[0;31m"
GREEN="\033[0;32m"
YELLOW="\033[0;33m"
BLUE="\033[0;34m"
CYAN="\033[0;36m"
NC="\033[0m"  # No Color

# Paths
ORANGE_ROOT="/opt/orange"
FFMPEG_ROOT="${ORANGE_ROOT}/lib/ffmpeg-nvidia"
OPENCV_ROOT="${ORANGE_ROOT}/lib/opencv"
TENSORRT_ROOT="/usr/local/TensorRT-10.0.1.6"
CUDA_ROOT="/usr/local/cuda"
CONFIG_FILE="system_config.yml"

# Counters
errors=0
warnings=0

# Debug flags
DRY_RUN=false
VERBOSE=false

# Log file setup
LOG_DIR="logs"
LOG_FILE="${LOG_DIR}/orange_check_$(date '+%Y%m%d_%H%M%S').log"

# Create logs directory if it doesn't exist
mkdir -p "$LOG_DIR"

# Function to log with timestamp while preserving color output
log_setup() {
    # Create a named pipe for logging
    if [[ ! -p /tmp/orange_check_pipe ]]; then
        mkfifo /tmp/orange_check_pipe
    fi

    # Start background process to handle logging
    tee -a "$LOG_FILE" < /tmp/orange_check_pipe &
    exec 3>&1 4>&2
    exec 1>/tmp/orange_check_pipe 2>&1
    
    # Log script start
    echo "==============================================="
    echo "Orange System Check Log - $(date)"
    echo "==============================================="
    echo "Host: $(hostname)"
    echo "User: $(whoami)"
    echo "Command: $0 $*"
    echo "==============================================="
}

# Function to clean up logging
log_cleanup() {
    local exit_code=$?
    exec 1>&3 2>&4
    rm -f /tmp/orange_check_pipe
    
    # Fix log file ownership if running as root
    if [ "$EUID" -eq 0 ] && [ -n "$SUDO_USER" ]; then
        chown "$SUDO_USER:$SUDO_USER" "$LOG_FILE"
        chown -R "$SUDO_USER:$SUDO_USER" "$LOG_DIR"
    fi
    
    echo -e "${BLUE}Log file created: $LOG_FILE${NC}"
    exit $exit_code
}

# Set up trap to cleanup on script exit
trap log_cleanup EXIT

# Usage information
usage() {
    echo "Usage: $0 [OPTIONS]"
    echo "Check Orange system dependencies and configuration."
    echo ""
    echo "Options:"
    echo "  -d, --dry-run     Show what would be checked without making changes"
    echo "  -v, --verbose     Show detailed output for all checks"
    echo "  -h, --help        Show this help message"
    echo ""
    echo "Example:"
    echo "  $0 --dry-run --verbose"
}

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        -d|--dry-run)
            DRY_RUN=true
            shift
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            usage
            exit 1
            ;;
    esac
done

# Initialize logging
log_setup "$@"  # Pass all script arguments to log

# Check if script is run as root
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}Please run as root${NC}"
    exit 1
fi

# Debug output function
debug() {
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}DEBUG: $1${NC}"
    fi
}

# Dry run output function
dry_run() {
    if [[ "$DRY_RUN" == true ]]; then
        echo -e "${YELLOW}WOULD RUN: $1${NC}"
        return 0
    fi
    return 1
}

# Version comparison function
version_compare() {
    local version=$1
    local expected=$2
    
    debug "Comparing versions: '$version' with '$expected'"
    
    # Strip any leading/trailing whitespace and 'release' text
    version=$(echo $version | sed 's/release //g' | tr -d '[:space:]')
    expected=$(echo $expected | tr -d '[:space:]')
    
    if [ "$version" = "$expected" ]; then
        debug "Version match successful"
        return 0
    fi
    debug "Version mismatch"
    return 1
}

# Library check function
check_library() {
    local lib_path="$1"
    local lib_name="$2"
    
    debug "Checking library: $lib_name at $lib_path"
    
    if dry_run "Check library $lib_name"; then
        return 0
    fi

    # First check for the exact file
    if [ -e "$lib_path" ]; then
        basic_path="$lib_path"
        debug "Found exact library path: $basic_path"
    else
        # Then check for versioned libraries
        basic_path=$(ls ${lib_path}.* 2>/dev/null | head -n1)
        debug "Searching for versioned library, found: $basic_path"
    fi

    if [ -n "$basic_path" ]; then
        echo -e "${GREEN}✓ Found $lib_name: $basic_path${NC}"
        
        if [[ "$VERBOSE" == true ]]; then
            echo -e "${CYAN}Library details:${NC}"
            file "$basic_path"
            echo -e "${CYAN}Dependencies:${NC}"
            ldd "$basic_path" | grep -v "not found"
        fi
        
        if ldd "$basic_path" &>/dev/null; then
            echo -e "${GREEN}  ✓ Verified shared library${NC}"
        else
            echo -e "${RED}  ✗ Error: Not a valid shared library${NC}"
            let errors++
        fi
    else
        echo -e "${RED}✗ Missing $lib_name: $lib_path${NC}"
        let errors++
    fi
}

# Executable check function
check_executable() {
    local exec_path="$1"
    local exec_name="$2"
    local version_flag="$3"
    local version_pattern="$4"
    local expected_version="$5"

    debug "Checking executable: $exec_name at $exec_path"
    
    if dry_run "Check executable $exec_name"; then
        return 0
    fi

    if [ -x "$exec_path" ]; then
        echo -e "${GREEN}✓ Found $exec_name: $exec_path${NC}"
        
        if [[ "$VERBOSE" == true ]]; then
            echo -e "${CYAN}File information:${NC}"
            file "$exec_path"
            echo -e "${CYAN}Full version output:${NC}"
            "$exec_path" $version_flag
        fi

        local version_output=$("$exec_path" $version_flag 2>&1)
        if [[ $version_output =~ $version_pattern ]]; then
            version="${BASH_REMATCH[1]}"
            debug "Extracted version: $version"
            if [ -n "$expected_version" ]; then
                if version_compare "$version" "$expected_version"; then
                    echo -e "${GREEN}  ✓ Version matches: $version${NC}"
                else
                    echo -e "${RED}  ✗ Version mismatch: found $version, expected $expected_version${NC}"
                    let errors++
                fi
            else
                echo -e "${GREEN}  ✓ Version: $version${NC}"
            fi
        else
            echo -e "${YELLOW}  ⚠ Could not determine version${NC}"
            debug "Version pattern did not match output:"
            debug "$version_output"
            let warnings++
        fi
    else
        echo -e "${RED}✗ Missing $exec_name: $exec_path${NC}"
        let errors++
    fi
}

# New function to check NVENC configuration
check_nvenc_configuration() {
    echo -e "\n${BLUE}Checking NVENC Configuration:${NC}"
    
    # Check if nvidia-smi can detect GPUs
    if ! command -v nvidia-smi &> /dev/null; then
        echo -e "${RED}✗ nvidia-smi not found. NVIDIA driver may not be installed.${NC}"
        let errors++
        return
    fi

    # Check if any NVIDIA GPUs are detected
    local gpu_count=$(nvidia-smi --query-gpu=gpu_name --format=csv,noheader | wc -l)
    if [ "$gpu_count" -eq 0 ]; then
        echo -e "${RED}✗ No NVIDIA GPUs detected${NC}"
        let errors++
        return
    fi

    # Check NVENC support in GPUs
    echo -e "${YELLOW}Checking NVENC support on available GPUs...${NC}"
    local nvenc_capable=false
    while IFS= read -r gpu_name; do
        # Check if GPU name contains known NVENC-capable series
        if [[ "$gpu_name" =~ (RTX|GTX|TITAN|Tesla|Quadro|A16) ]]; then
            echo -e "${GREEN}✓ Found NVENC-capable GPU: $gpu_name${NC}"
            nvenc_capable=true
        else
            echo -e "${YELLOW}⚠ GPU may not support NVENC: $gpu_name${NC}"
            let warnings++
        fi
    done < <(nvidia-smi --query-gpu=gpu_name --format=csv,noheader)

    if [ "$nvenc_capable" = false ]; then
        echo -e "${RED}✗ No NVENC-capable GPUs detected${NC}"
        let errors++
        return
    fi

    # Check FFmpeg NVENC support
    if [ -x "$FFMPEG_ROOT/bin/ffmpeg" ]; then
        echo -e "\n${YELLOW}Checking FFmpeg NVENC support...${NC}"
        local nvenc_encoders=$("$FFMPEG_ROOT/bin/ffmpeg" -encoders 2>/dev/null | grep -i nvenc)
        if [ -n "$nvenc_encoders" ]; then
            echo -e "${GREEN}✓ FFmpeg NVENC encoders found:${NC}"
            if [[ "$VERBOSE" == true ]]; then
                echo "$nvenc_encoders"
            else
                echo "$nvenc_encoders" | head -n 6
            fi
        else
            echo -e "${RED}✗ No NVENC encoders found in FFmpeg${NC}"
            let errors++
        fi
    else
        echo -e "${RED}✗ FFmpeg not found at $FFMPEG_ROOT/bin/ffmpeg${NC}"
        let errors++
    fi

    # Check NVIDIA driver version
    local driver_version=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
    echo -e "\n${YELLOW}Checking NVIDIA driver version...${NC}"
    if [[ "$driver_version" =~ ^[0-9]+\.[0-9]+ ]]; then
        local major_version="${BASH_REMATCH[0]}"
        if (( $(echo "$major_version >= 450.0" | bc -l) )); then
            echo -e "${GREEN}✓ NVIDIA driver version $driver_version is compatible with NVENC${NC}"
        else
            echo -e "${RED}✗ NVIDIA driver version $driver_version may be too old for optimal NVENC support${NC}"
            echo -e "${YELLOW}  ⚠ Recommended version is 450.0 or higher${NC}"
            let warnings++
        fi
    else
        echo -e "${RED}✗ Could not determine NVIDIA driver version${NC}"
        let errors++
    fi
}

# GPU Configuration check
check_gpu_configuration() {
    echo -e "\n${BLUE}Checking NVIDIA GPU Configuration:${NC}"
    
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}Full nvidia-smi output:${NC}"
        nvidia-smi
        
        echo -e "\n${CYAN}GPU NUMA information:${NC}"
        nvidia-smi topo -m
        
        echo -e "\n${CYAN}PCI Bus Information:${NC}"
        lspci | grep -i nvidia
        
        echo -e "\n${CYAN}Detailed GPU Information:${NC}"
        nvidia-smi --query-gpu=gpu_name,memory.total,memory.free,power.draw,power.limit,temperature.gpu --format=csv
    fi

    driver_version=$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n1)
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ NVIDIA driver version: $driver_version${NC}"
        
        echo -e "\nGPU Information:"
        nvidia-smi --query-gpu=gpu_name,memory.total,power.limit --format=csv,noheader | while IFS=, read -r name memory power; do
            echo -e "${GREEN}✓ $name | Memory: $memory | Power Limit: $power${NC}"
        done
    else
        echo -e "${RED}✗ NVIDIA driver not found or not functioning${NC}"
        let errors++
    fi
}

# CUDA Installation check
check_cuda_installation() {
    echo -e "\n${BLUE}Checking CUDA Installation:${NC}"
    
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}CUDA Installation Details:${NC}"
        echo "CUDA_ROOT: $CUDA_ROOT"
        ls -l $CUDA_ROOT
        
        echo -e "\n${CYAN}CUDA Version Information:${NC}"
        cat $CUDA_ROOT/version.txt 2>/dev/null
        
        echo -e "\n${CYAN}CUDA Sample Utility Output:${NC}"
        if [ -x "$CUDA_ROOT/extras/demo_suite/deviceQuery" ]; then
            $CUDA_ROOT/extras/demo_suite/deviceQuery
        fi
    fi

    if [ -d "$CUDA_ROOT" ]; then
        echo -e "${GREEN}✓ Found CUDA installation: $CUDA_ROOT${NC}"
        check_executable "$CUDA_ROOT/bin/nvcc" "CUDA Compiler" "--version" "V([0-9.]+)" "12.2.140"
    else
        echo -e "${RED}✗ CUDA installation not found${NC}"
        let errors++
    fi
}

# OpenCV Installation check
check_opencv_installation() {
    echo -e "\n${BLUE}Checking OpenCV Installation:${NC}"
    
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}OpenCV Installation Details:${NC}"
        echo "OpenCV Root: $OPENCV_ROOT"
        ls -l $OPENCV_ROOT 2>/dev/null
        
        echo -e "\n${CYAN}OpenCV pkg-config Information:${NC}"
        pkg-config --cflags opencv4 2>/dev/null
        pkg-config --libs opencv4 2>/dev/null
    fi

    check_library "$OPENCV_ROOT/lib/libopencv_core.so" "OpenCV Core"
    check_library "$OPENCV_ROOT/lib/libopencv_imgproc.so" "OpenCV ImgProc"
    check_library "$OPENCV_ROOT/lib/libopencv_imgcodecs.so" "OpenCV ImgCodecs"
    check_library "$OPENCV_ROOT/lib/libopencv_sfm.so" "OpenCV SFM"

    if pkg-config --exists opencv4; then
        opencv_version=$(pkg-config --modversion opencv4)
        echo -e "${GREEN}✓ OpenCV version: $opencv_version${NC}"
    else
        echo -e "${RED}✗ OpenCV pkg-config not found${NC}"
        let errors++
    fi
}

# FFmpeg Installation check
check_ffmpeg_installation() {
    echo -e "\n${BLUE}Checking FFmpeg Installation:${NC}"
    
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}FFmpeg Installation Details:${NC}"
        echo "FFmpeg Root: $FFMPEG_ROOT"
        ls -l $FFMPEG_ROOT
        
        if [ -x "$FFMPEG_ROOT/bin/ffmpeg" ]; then
            echo -e "\n${CYAN}FFmpeg Version Information:${NC}"
            $FFMPEG_ROOT/bin/ffmpeg -version
            
            echo -e "\n${CYAN}Available Encoders:${NC}"
            $FFMPEG_ROOT/bin/ffmpeg -encoders | grep -i nvidia
            
            echo -e "\n${CYAN}Available Decoders:${NC}"
            $FFMPEG_ROOT/bin/ffmpeg -decoders | grep -i nvidia
        fi
    fi

    # Updated version pattern to match "n4.4.5-7-g283dc2e8eb"
    check_executable "$FFMPEG_ROOT/bin/ffmpeg" "FFmpeg" "-version" "ffmpeg version n([0-9]+\.[0-9]+)" "4.4"
    check_library "$FFMPEG_ROOT/lib/libavcodec.so" "FFmpeg libavcodec"
    check_library "$FFMPEG_ROOT/lib/libavformat.so" "FFmpeg libavformat"
    check_library "$FFMPEG_ROOT/lib/libavutil.so" "FFmpeg libavutil"

    if [ -d "$FFMPEG_ROOT/include" ]; then
        echo -e "${GREEN}✓ FFmpeg headers found${NC}"
    else
        echo -e "${RED}✗ FFmpeg headers missing${NC}"
        let errors++
    fi
}

# TensorRT Installation check
check_tensorrt_installation() {
    echo -e "\n${BLUE}Checking TensorRT Installation:${NC}"
    
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}TensorRT Installation Details:${NC}"
        echo "TensorRT Root: $TENSORRT_ROOT"
        ls -l $TENSORRT_ROOT 2>/dev/null
    fi

    if [ -d "$TENSORRT_ROOT" ]; then
        echo -e "${GREEN}✓ Found TensorRT installation: $TENSORRT_ROOT${NC}"
        check_library "$TENSORRT_ROOT/lib/libnvinfer.so" "TensorRT libnvinfer"
        check_library "$TENSORRT_ROOT/lib/libnvinfer_plugin.so" "TensorRT libnvinfer_plugin"
    else
        echo -e "${RED}✗ TensorRT installation not found${NC}"
        let errors++
    fi
}

# Network interface check
check_network_interfaces() {
    echo -e "\n${BLUE}Checking Network Interfaces:${NC}"
    local config_path="$(dirname $0)/system_config.yml"
    
    debug "Looking for config file at: $config_path"
    
    if [[ "$VERBOSE" == true ]]; then
        echo -e "${CYAN}Network Interface Details:${NC}"
        ip addr show
        
        echo -e "\n${CYAN}Network Interface Status:${NC}"
        ip -s link
    fi
    
    if [ -f "$config_path" ]; then
        if [ -f "./check_network_settings.py" ]; then
            if [[ "$VERBOSE" == true ]]; then
                debug "Running Python network check script with verbose output"
                python3 ./check_network_settings.py "$config_path" --verbose
            else
                python3 ./check_network_settings.py "$config_path"
            fi
            local python_exit=$?
            if [ $python_exit -ne 0 ]; then
                let errors+=$python_exit
            fi
        else
            echo -e "${RED}✗ Network check script (check_network_settings.py) not found${NC}"
            let errors++
        fi
    else
        echo -e "${RED}✗ Configuration file not found: $config_path${NC}"
        echo -e "${YELLOW}  ⚠ Please ensure system_config.yml is in the same directory as this script${NC}"
        let errors++
    fi
}

# Main script execution
echo -e "${BLUE}Verifying Orange project dependencies...${NC}\n"

if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}Running in DRY-RUN mode - no actual checks will be performed${NC}"
fi

if [[ "$VERBOSE" == true ]]; then
    echo -e "${CYAN}Running in VERBOSE mode - detailed output will be shown${NC}"
fi

# Log script start time
if [[ "$VERBOSE" == true ]]; then
    echo -e "${CYAN}Check started at: $(date)${NC}"
    echo -e "${CYAN}Running on host: $(hostname)${NC}"
    echo -e "${CYAN}System information:${NC}"
    uname -a
fi

# Run all checks
check_gpu_configuration
check_nvenc_configuration
check_cuda_installation
check_opencv_installation
check_ffmpeg_installation
check_tensorrt_installation
check_network_interfaces

# Print summary
echo -e "\n${BLUE}Verification Summary:${NC}"
if [[ "$DRY_RUN" == true ]]; then
    echo -e "${YELLOW}DRY-RUN complete - no actual checks were performed${NC}"
elif [ $errors -eq 0 ] && [ $warnings -eq 0 ]; then
    echo -e "${GREEN}✓ All dependencies are properly installed and configured!${NC}"
    [[ "$VERBOSE" == true ]] && echo -e "${CYAN}No errors or warnings encountered during verification${NC}"
    exit 0
elif [ $errors -eq 0 ]; then
    echo -e "${YELLOW}⚠ Found $warnings warning(s) but no critical errors${NC}"
    [[ "$VERBOSE" == true ]] && echo -e "${CYAN}Warnings occurred but no critical errors found${NC}"
    exit 0
else
    echo -e "${RED}✗ Found $errors error(s) and $warnings warning(s)${NC}"
    echo -e "${YELLOW}Please address the issues above before proceeding${NC}"
    [[ "$VERBOSE" == true ]] && echo -e "${CYAN}Critical errors were encountered during verification${NC}"
    exit 1
fi

# Log script end time if verbose
if [[ "$VERBOSE" == true ]]; then
    echo -e "${CYAN}Check completed at: $(date)${NC}"
fi