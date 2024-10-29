#!/bin/bash

# ANSI color codes
RED="\033[0;31m"
GREEN="\033[0;32m"
BLUE="\033[0;34m"
NC="\033[0m"  # No Color

# If someone runs with sudo, warn them it's not needed
if [ "$EUID" -eq 0 ]; then
  echo -e "${YELLOW}Warning: This script does not need to be run with sudo.${NC}"
  echo -e "${YELLOW}Running git commands with sudo can cause permission issues.${NC}"
  exit 1
fi

# Clone the repo and submodules to the home directory
INSTALL_DIR="$HOME/orange"

echo -e "${BLUE}Cloning the repository to ${INSTALL_DIR}...${NC}"
git clone git@github.com:JohnsonLabJanelia/orange.git "$INSTALL_DIR" || { 
  echo -e "${RED}Failed to clone repository. Please check your SSH keys and permissions.${NC}"
  exit 1
}

cd "$INSTALL_DIR" || { 
  echo -e "${RED}Failed to enter repository directory.${NC}"
  exit 1
}

echo -e "${BLUE}Initializing and updating submodules...${NC}"
git submodule init || {
  echo -e "${RED}Failed to initialize submodules.${NC}"
  exit 1
}

git submodule update || {
  echo -e "${RED}Failed to update submodules.${NC}"
  exit 1
}

echo -e "${GREEN}Repository cloned successfully. You can now build the project using the build script.${NC}"