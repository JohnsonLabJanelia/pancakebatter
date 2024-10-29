# #!/bin/bash

# # ANSI color codes
# RED='\033[0;31m'
# GREEN='\033[0;32m'
# YELLOW='\033[0;33m'
# BLUE='\033[0;36m'
# NC='\033[0m'

# OPENCV_DIR="/opt/orange/lib/opencv"
# PROFILE_FILE="/etc/profile.d/opencv-orange.sh"

# echo -e "${BLUE}Updating OpenCV profile configuration...${NC}"

# # Create updated profile script
# cat > /tmp/opencv-orange.sh << EOF
# # OpenCV configuration for Orange project
# export OPENCV_DIR=${OPENCV_DIR}
# export OPENCV_HOME=\${OPENCV_DIR}
# export PATH=\${OPENCV_HOME}/bin:\${PATH}
# export LD_LIBRARY_PATH=\${OPENCV_HOME}/lib:\${LD_LIBRARY_PATH}
# export PKG_CONFIG_PATH=\${OPENCV_HOME}/lib/pkgconfig:\${PKG_CONFIG_PATH}
# EOF

# # Install the updated profile script
# sudo cp /tmp/opencv-orange.sh "$PROFILE_FILE"
# sudo chmod 644 "$PROFILE_FILE"

# # Create pkg-config directory if it doesn't exist
# if [ ! -d "${OPENCV_DIR}/lib/pkgconfig" ]; then
#     echo -e "${YELLOW}Creating pkg-config directory...${NC}"
#     sudo mkdir -p "${OPENCV_DIR}/lib/pkgconfig"
# fi

# # Create opencv4.pc if it doesn't exist
# if [ ! -f "${OPENCV_DIR}/lib/pkgconfig/opencv4.pc" ]; then
#     echo -e "${YELLOW}Creating opencv4.pc...${NC}"
#     cat > /tmp/opencv4.pc << EOF
# prefix=${OPENCV_DIR}
# exec_prefix=\${prefix}
# libdir=\${prefix}/lib
# includedir=\${prefix}/include/opencv4

# Name: OpenCV
# Description: Open Source Computer Vision Library
# Version: 4.8.0
# Requires:
# Libs: -L\${libdir} -lopencv_core -lopencv_imgproc -lopencv_imgcodecs -lopencv_sfm
# Libs.private: -ldl -lm -lpthread -lrt
# Cflags: -I\${includedir}
# EOF
#     sudo cp /tmp/opencv4.pc "${OPENCV_DIR}/lib/pkgconfig/"
#     sudo chmod 644 "${OPENCV_DIR}/lib/pkgconfig/opencv4.pc"
# fi

# # Clean up
# rm -f /tmp/opencv-orange.sh /tmp/opencv4.pc

# # Source the new profile
# source "$PROFILE_FILE"

# # Verify the setup
# echo -e "${BLUE}Verifying OpenCV configuration...${NC}"

# if [ -n "$OPENCV_DIR" ]; then
#     echo -e "${GREEN}✓ OPENCV_DIR is set to: $OPENCV_DIR${NC}"
# else
#     echo -e "${RED}✗ OPENCV_DIR is not set${NC}"
# fi

# if pkg-config --exists opencv4; then
#     echo -e "${GREEN}✓ OpenCV pkg-config is properly configured${NC}"
#     echo -e "\n${BLUE}OpenCV Information:${NC}"
#     echo -e "${YELLOW}Version:${NC} $(pkg-config --modversion opencv4)"
#     echo -e "${YELLOW}Compile flags:${NC} $(pkg-config --cflags opencv4)"
#     echo -e "${YELLOW}Link flags:${NC} $(pkg-config --libs opencv4)"
# else
#     echo -e "${RED}✗ OpenCV pkg-config is not properly configured${NC}"
# fi

# echo -e "\n${YELLOW}Note: Changes have been made to $PROFILE_FILE${NC}"
# echo -e "${YELLOW}Please run 'source $PROFILE_FILE' or log out and back in for changes to take effect.${NC}"

#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;36m'
NC='\033[0m'

OPENCV_DIR="/opt/orange/lib/opencv"
SYSTEM_PKGCONFIG="/usr/lib/x86_64-linux-gnu/pkgconfig"

echo -e "${BLUE}Creating system-wide pkg-config link for OpenCV...${NC}"

# First ensure the OpenCV pkg-config file exists
if [ ! -f "${OPENCV_DIR}/lib/pkgconfig/opencv4.pc" ]; then
    echo -e "${RED}OpenCV pkg-config file not found in ${OPENCV_DIR}/lib/pkgconfig/${NC}"
    exit 1
fi

# Create symbolic link in system pkgconfig directory
sudo ln -sf "${OPENCV_DIR}/lib/pkgconfig/opencv4.pc" "${SYSTEM_PKGCONFIG}/opencv4.pc"

# Update the sudoers configuration to preserve PKG_CONFIG_PATH
SUDOERS_FILE="/etc/sudoers.d/preserve_pkg_config"
echo -e "${BLUE}Updating sudoers to preserve PKG_CONFIG_PATH...${NC}"

echo 'Defaults env_keep += "PKG_CONFIG_PATH"' | sudo EDITOR='tee' visudo -f "$SUDOERS_FILE"

# Verify the installation
echo -e "${BLUE}Verifying pkg-config setup...${NC}"

# Test as regular user
echo -e "${YELLOW}Testing as regular user:${NC}"
if pkg-config --exists opencv4; then
    echo -e "${GREEN}✓ OpenCV pkg-config found (user context)${NC}"
    echo -e "Version: $(pkg-config --modversion opencv4)"
else
    echo -e "${RED}✗ OpenCV pkg-config not found (user context)${NC}"
fi

# Test as sudo
echo -e "\n${YELLOW}Testing as sudo:${NC}"
if sudo pkg-config --exists opencv4; then
    echo -e "${GREEN}✓ OpenCV pkg-config found (sudo context)${NC}"
    echo -e "Version: $(sudo pkg-config --modversion opencv4)"
else
    echo -e "${RED}✗ OpenCV pkg-config not found (sudo context)${NC}"
fi

echo -e "\n${YELLOW}Note: Please run your dependency check script again to verify the fix.${NC}"