#!/bin/bash

# ANSI color codes for formatting output
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m' # No Color (Reset)

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: Please run this script as root or with sudo.${NC}"
    echo ""
    exit 1
fi

# Dynamically find the eSDK folder (look for a folder that contains 'eSDK' in its name)
ESDK_FOLDER=$(find . -maxdepth 1 -type d -name "*eSDK*")

# Check if the eSDK folder was found
if [ -z "$ESDK_FOLDER" ]; then
    echo ""
    echo -e "${RED}ERROR: eSDK folder not found in the current directory.${NC}"
    echo ""
    exit 1
fi

# Navigate to the eSDK folder
echo "Navigating to eSDK folder: $ESDK_FOLDER"
cd "$ESDK_FOLDER" || { echo -e "${RED}ERROR: Failed to change directory to $ESDK_FOLDER.${NC}"; exit 1; }

# Run the uninstall script for eSDK
if [ -f "./uninstall_eSdk.sh" ]; then
    echo ""
    echo "Running uninstall_eSDK.sh..."
    sudo ./uninstall_eSdk.sh
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}eSDK successfully uninstalled.${NC}"
        echo ""
    else
        echo ""
        echo -e "${RED}ERROR: Failed to run uninstall_eSDK.sh.${NC}"
        echo ""
        exit 1
    fi
else
    echo ""
    echo -e "${RED}ERROR: uninstall_eSDK.sh script not found in $ESDK_FOLDER.${NC}"
    echo ""
    exit 1
fi


# Check if the uninstallation was successful
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}Emergent eSDK (emergent-esdk-ecapture) successfully removed.${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}ERROR: Failed to remove Emergent eSDK (emergent-esdk-ecapture).${NC}"
    echo ""
    exit 1
fi

# Now finally clean up the eSDK and eCapture folders
echo ""
echo "Cleaning up eSDK, eCapture, and Rivermax License directories..."
cd ..
if [ -d "eSDK" ]; then
    echo ""
    echo "Removing eSDK directory..."
    rm -rf eSDK
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}eSDK directory successfully removed.${NC}"
        echo ""
    else
        echo ""
        echo -e "${RED}ERROR: Failed to remove eSDK directory.${NC}"
        echo ""
        exit 1
    fi
fi

# Return to the original directory before removing eSDK, eCapture, and license folders
cd "$ORIGINAL_DIRECTORY" || { echo -e "${RED}Error: Failed to return to original directory.${NC}"; exit 1; }


if [ -d "eCap*" ]; then
    echo ""
    echo "Removing eCapture files..."
    rm -rf eCap*
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}eCapture files successfully removed.${NC}"
        echo ""
    else
        echo ""
        echo -e "${RED}ERROR: Failed to remove eCapture files.${NC}"
        echo ""
        exit 1
    fi
fi

if [ -d "rivermax_license" ]; then
    echo ""
    echo "Removing Rivermax License directory..."
    rm -rf rivermax_license
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}Rivermax License directory successfully removed.${NC}"
        echo ""
    else
        echo ""
        echo -e "${RED}ERROR: Failed to remove Rivermax License directory.${NC}"
        echo ""
        exit 1
    fi
fi

# Remove the /opt/EVT folder
if [ -d "/opt/EVT" ]; then
    echo ""
    echo "Removing /opt/EVT directory..."
    sudo rm -rf /opt/EVT
    if [ $? -eq 0 ]; then
        echo ""
        echo -e "${GREEN}/opt/EVT directory successfully removed.${NC}"
        echo ""
    else
        echo ""
        echo -e "${RED}ERROR: Failed to remove /opt/EVT directory.${NC}"
        echo ""
        exit 1
    fi
fi

# Notify the user of the successful cleanup
echo ""
echo -e "${GREEN}Cleanup completed.${NC}"
echo ""
echo ""

exit 0
