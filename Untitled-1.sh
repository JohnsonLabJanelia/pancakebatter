# #!/bin/bash

# # CUDA Environment Diagnostic Script
# # Created by Claude for Jeremy Delahanty, October 2024

# # ANSI color codes
# RED='\033[0;31m'
# GREEN='\033[0;32m'
# YELLOW='\033[1;33m'
# BLUE='\033[0;34m'
# NC='\033[0m' # No Color

# echo -e "${BLUE}=== CUDA Environment Diagnostic ===${NC}"

# # Check CUDA installation directory
# echo -e "\n${YELLOW}1. Checking CUDA Installation Directory:${NC}"
# if [ -d "/usr/local/cuda" ]; then
#     echo -e "${GREEN}✓ CUDA directory exists at /usr/local/cuda${NC}"
#     ls -l /usr/local/cuda
# else
#     echo -e "${RED}✗ CUDA directory not found at /usr/local/cuda${NC}"
# fi

# # Check CUDA symlink
# echo -e "\n${YELLOW}2. Checking CUDA Symlink:${NC}"
# if [ -L "/usr/local/cuda" ]; then
#     echo -e "${GREEN}✓ CUDA is a symlink pointing to:${NC}"
#     ls -l /usr/local/cuda
# fi

# # Check nvcc in various locations
# echo -e "\n${YELLOW}3. Looking for nvcc:${NC}"
# locations=(
#     "/usr/local/cuda/bin/nvcc"
#     "/usr/bin/nvcc"
#     "/usr/local/bin/nvcc"
# )

# for loc in "${locations[@]}"; do
#     if [ -f "$loc" ]; then
#         echo -e "${GREEN}✓ Found nvcc at: $loc${NC}"
#         ls -l "$loc"
#     else
#         echo -e "${RED}✗ No nvcc at: $loc${NC}"
#     fi
# done

# # Check if nvcc is in PATH
# echo -e "\n${YELLOW}4. Checking nvcc in PATH:${NC}"
# which nvcc
# if [ $? -eq 0 ]; then
#     echo -e "${GREEN}✓ nvcc found in PATH${NC}"
# else
#     echo -e "${RED}✗ nvcc not found in PATH${NC}"
# fi

# # Print current PATH
# echo -e "\n${YELLOW}5. Current PATH:${NC}"
# echo "$PATH" | tr ':' '\n'

# # Try to run nvcc
# echo -e "\n${YELLOW}6. Attempting to run nvcc:${NC}"
# if command -v nvcc >/dev/null 2>&1; then
#     echo -e "${GREEN}✓ nvcc command found${NC}"
#     nvcc --version
# else
#     echo -e "${RED}✗ nvcc command not found${NC}"
# fi

# # Check CUDA environment variables
# echo -e "\n${YELLOW}7. CUDA Environment Variables:${NC}"
# echo "CUDA_HOME=$CUDA_HOME"
# echo "CUDA_PATH=$CUDA_PATH"
# echo "CUDA_ROOT=$CUDA_ROOT"
# echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"

# # Check nvidia-smi
# echo -e "\n${YELLOW}8. Checking NVIDIA Driver:${NC}"
# if command -v nvidia-smi >/dev/null 2>&1; then
#     echo -e "${GREEN}✓ nvidia-smi available${NC}"
#     nvidia-smi --query-gpu=gpu_name,driver_version --format=csv,noheader
# else
#     echo -e "${RED}✗ nvidia-smi not found${NC}"
# fi

# # Try to compile a simple CUDA program
# echo -e "\n${YELLOW}9. Testing CUDA Compilation:${NC}"
# TEST_DIR=$(mktemp -d)
# cat > "$TEST_DIR/test.cu" << 'EOF'
# #include <stdio.h>

# int main() {
#     printf("CUDA test program\n");
#     return 0;
# }
# EOF

# cd "$TEST_DIR"
# if nvcc test.cu -o test 2>/dev/null; then
#     echo -e "${GREEN}✓ CUDA compilation successful${NC}"
#     ./test
# else
#     echo -e "${RED}✗ CUDA compilation failed${NC}"
# fi

# # Cleanup
# rm -rf "$TEST_DIR"

# echo -e "\n${BLUE}=== Diagnostic Complete ===${NC}"

##!/bin/bash

# # ANSI color codes
# RED='\033[0;31m'
# GREEN='\033[0;32m'
# NC='\033[0m'

# # Set CUDA environment
# CUDA_PATH="/usr/local/cuda"
# export PATH="$CUDA_PATH/bin:$PATH"
# export LD_LIBRARY_PATH="$CUDA_PATH/lib64:$LD_LIBRARY_PATH"
# export CUDA_HOME="$CUDA_PATH"

# echo -e "${GREEN}Testing CUDA environment...${NC}"
# echo "PATH=$PATH"
# echo "CUDA_HOME=$CUDA_HOME"
# echo ""

# echo -e "${GREEN}Testing nvcc...${NC}"
# nvcc --version
# echo ""

# echo -e "${GREEN}Testing simple CUDA compilation...${NC}"
# TEST_DIR=$(mktemp -d)
# cat > "$TEST_DIR/test.cu" << 'EOF'
# #include <stdio.h>

# int main() {
#     printf("CUDA test successful!\n");
#     return 0;
# }
# EOF

# cd "$TEST_DIR"
# nvcc test.cu -o test && ./test

# # Cleanup
# rm -rf "$TEST_DIR"
