#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color (Reset)

# Save the original directory where the script is started
ORIGINAL_DIRECTORY=$(pwd)

# Function to display usage information
usage() {
    echo "Usage: $0 [-n <nic_type>]"
    echo "  -n <nic_type>    Specify the NIC type to install (emergent or Mellanox)"
    exit 1
}

# Parse command-line arguments
while getopts "n:" opt; do
    case ${opt} in
        n )
            NIC_TYPE=$OPTARG
            ;;
        \? )
            usage
            ;;
    esac
done

# Validate NIC_TYPE
if [[ "$NIC_TYPE" != "emergent" && "$NIC_TYPE" != "Mellanox" ]]; then
    echo -e "${RED}ERROR: Invalid NIC type. Please specify either 'emergent' or 'Mellanox' (case sensitive).${NC}"
    usage
fi

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: Please run this script as sudo with -E.${NC}"
    echo -e "${RED}This script requires root permissions to install the Emergent Vision Technologies (EVT) drivers.${NC}"
    echo ""
    exit 1
fi

# Check if the SSH environment variable is preserved
if [ -z "$SSH_AUTH_SOCK" ]; then
    echo ""
    echo -e "${RED}ERROR: SSH environment not preserved. Please run the script with sudo -E.${NC}"
    echo "The -E flag is needed to preserve your SSH environment and avoid password prompts for scp."
    echo ""
    exit 1
fi

# Attempt download of Emergent software (eSDK and eCapturePro) from Johnson Lab Server
echo -e "${YELLOW}Attempting to download the eSDK and eCapturePro zip files from Johnson Lab Server.${NC}"
scp -r delahantyj@login1:/groups/johnson/johnsonlab/pancake_recipes/emergent/* .

# Download NIC-specific files
echo -e "${YELLOW} Downloading $NIC_TYPE NIC files. ${NC}"
scp -r delahantyj@login1:/groups/johnson/johnsonlab/pancake_recipes/nics/$NIC_TYPE/* .

# Check if the download was successful
if [ $? -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: Failed to download files from the server.${NC}"
    echo ""
    exit 1
fi

# Get the original user (the one who ran the script with sudo)
ORIGINAL_USER=$SUDO_USER

# Change ownership of files owned by root to the original user
echo "Changing ownership of files owned by root to $ORIGINAL_USER"
find . -user root -exec chown "$ORIGINAL_USER":"$ORIGINAL_USER" {} \;

# Find all zip files in the current directory
echo "Searching for zip files in: $(pwd)"
ZIP_FILES=(*.zip)

# Check for the number of zip files found
if [ ${#ZIP_FILES[@]} -eq 0 ]; then # If there are no zip files found, tell the user
    echo -e "${RED}ERROR: No .zip files found in the current directory.${NC}"
    exit 1
fi

# Move the Rivermax license file to correct directory (only for Mellanox)
if [ "$NIC_TYPE" == "Mellanox" ]; then
    echo -e "${YELLOW}Moving the Rivermax license file to the correct directory.${NC}"
    if [ -f "rivermax.lic" ]; then
        if sudo mv rivermax.lic /opt/mellanox/rivermax/; then
            echo -e "${GREEN}Rivermax License successfully moved.${NC}"
        else
            echo -e "${RED}ERROR: Failed to copy Rivermax License.${NC}"
        fi
    else
        echo -e "${RED}ERROR: Rivermax License file not found.${NC}"
    fi
fi

# Iterate over each zip file
for ZIP_FILE in "${ZIP_FILES[@]}"; do
    echo -e "${YELLOW}Processing zip file: ${GREEN}$ZIP_FILE${NC}"

    # Define the target directory
    TARGET_DIR="${ZIP_FILE%.zip}"  # Remove the .zip extension for the directory name

    # Create the target directory
    echo -e "${YELLOW}Creating directory: ${GREEN}$TARGET_DIR${NC}"
    mkdir -p "$TARGET_DIR"

    # Unzip the zip file into the target directory
    echo -e "${YELLOW}Extracting files...${NC}"
    if unzip "$ZIP_FILE" -d "$TARGET_DIR"; then
        echo -e "${GREEN}Extraction successful.${NC}"
    else
        echo -e "${RED}Error: Failed to extract $ZIP_FILE${NC}"
        continue
    fi

    # Change ownership of the extracted files owned by root to the original user
    echo -e "${YELLOW}Changing file ownership...${NC}"
    find "$TARGET_DIR" -user root -exec chown "$ORIGINAL_USER":"$ORIGINAL_USER" {} \;

    # Change into the target directory
    echo -e "${YELLOW}Changing to directory: ${GREEN}$TARGET_DIR${NC}"
    cd "$TARGET_DIR" || { echo -e "${RED}Error: Failed to change directory to $TARGET_DIR${NC}"; exit 1; }

    # Print the current directory to confirm for user
    echo -e "${GREEN}Current directory: $(pwd)${NC}"

    # Check for the filename pattern and execute commands accordingly
    if [[ "$ZIP_FILE" == *"eSDK"* ]]; then
        # eSDK related commands
        echo -e "${YELLOW}Detected eSDK in the filename. Proceeding with eSDK installation.${NC}"

        # Install drivers based on NIC_TYPE
        if [ "$NIC_TYPE" == "Mellanox" ]; then
            echo -e "${YELLOW}Installing Mellanox drivers...${NC}"
            if sudo ./install_eSdk.sh -i Mellanox; then
                echo -e "${GREEN}Mellanox drivers installed successfully.${NC}"
            else
                echo -e "${RED}Error: Failed to install Mellanox drivers.${NC}"
            fi
        elif [ "$NIC_TYPE" == "emergent" ]; then
            echo -e "${YELLOW}Installing Emergent drivers...${NC}"
            if sudo ./install_eSdk.sh -i emergent; then
                echo -e "${GREEN}Emergent drivers installed successfully.${NC}"
            else
                echo -e "${RED}Error: Failed to install Emergent drivers.${NC}"
            fi
        fi
    fi

    # Return to the parent directory after each iteration
    cd ..
    echo -e "${YELLOW}Returned to parent directory.${NC}"
    echo -e "${GREEN}------------------------------${NC}"
done

# Return to the original directory before removing zip files
cd "$ORIGINAL_DIRECTORY" || { echo -e "${RED}Error: Failed to return to original directory.${NC}"; exit 1; }

# Cleanup section
echo -e "${YELLOW}Cleaning up installation files...${NC}"
echo -e "${YELLOW}Removing zip files from directory.${NC}"
if rm *.zip; then
    echo -e "${GREEN}Zip files removed successfully.${NC}"
else
    echo -e "${RED}Error: Failed to remove zip files. Please check and remove them manually.${NC}"
fi

# Starting HCA Driver, recommended from Installation of the eSDK
echo -e "${YELLOW}Restarting HCA Driver...${NC}"

# Attempt to restart the HCA Driver
if sudo /etc/init.d/openibd restart; then
    echo -e "${GREEN}HCA Driver successfully restarted.${NC}"
else
    echo -e "${RED}ERROR: Failed to restart HCA Driver. Please check the logs or configuration.${NC}"
fi

# Indicate to user that script completed successfully
echo -e "${GREEN}Emergent Vision Technologies (EVT) software installation completed successfully!${NC}"

exit 0