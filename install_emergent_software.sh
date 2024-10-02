#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color (Reset)

# Save the original directory where the script is started
ORIGINAL_DIRECTORY=$(pwd)

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: Please run this script as sudo with -E.${NC}"
    echo -e "This script requires root permissions to install the Emergent Vision Technologies (EVT) drivers.${NC}"
    echo ""
    exit 1
fi

# Check if the SSH environment variable is preserved
if [ -z "$SSH_AUTH_SOCK" ]; then
    echo ""
    echo -e "${RED}ERROR: SSH environment not preserved. Please run the script with sudo -E.${NC}"
    echo -e "The -E flag is needed to preserve your SSH environment and avoid password prompts for scp."
    echo ""
    exit 1
fi

# Attempt download of Emergent software (eSDK and eCapturePro) from Johnson Lab Server
echo "Attempting to download the eSDK and eCapturePro zip files from Johnson Lab Server"
scp -r delahantyj@login1:/groups/johnson/johnsonlab/pancake_recipes/emergent/* .

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

# Iterate over each zip file
for ZIP_FILE in "${ZIP_FILES[@]}"; do
    echo "Processing zip file: $ZIP_FILE"

    # Define the target directory
    TARGET_DIR="${ZIP_FILE%.zip}"  # Remove the .zip extension for the directory name

    # Create the target directory
    mkdir -p "$TARGET_DIR"

    # Unzip the zip file into the target directory
    unzip "$ZIP_FILE" -d "$TARGET_DIR"

    # Change ownership of the extracted files owned by root to the original user
    find "$TARGET_DIR" -user root -exec chown "$ORIGINAL_USER":"$ORIGINAL_USER" {} \;

    # Change into the target directory
    cd "$TARGET_DIR" || { echo -e "${RED}Error: Failed to change directory to $TARGET_DIR${NC}"; exit 1; }

    # Print the current directory to confirm for user
    echo "Changed directory to $(pwd)"

    # Check for the filename pattern and execute commands accordingly
    if [[ "$ZIP_FILE" == *"eSDK"* ]]; then
        # eSDK related commands
        echo ""
        echo "Detected eSDK in the filename. Proceeding with eSDK installation."
        echo ""

        # Install Mellanox and EVT drivers
        echo "Installing Mellanox and EVT drivers"
        sudo ./install_eSdk.sh -i Mellanox && sudo ./install_eSdk.sh -i EVT
        echo ""
        echo -e "${GREEN}Mellanox and EVT drivers installed.${NC}"
        echo ""

        # Move the rivermax license file to correct directory
        echo "Moving the Rivermax license file to the correct directory"

        # Attempt to copy the Rivermax license to correct directory
        if cd .. && [ -d "rivermax_license" ]; then
            echo ""
            echo -e "${GREEN}Rivermax License directory found.${NC}"
            echo ""
        else
            echo ""
            echo -e "${RED}ERROR: Rivermax License directory not found.${NC}"
            echo ""
            continue
        fi
    
        if sudo mv rivermax_license/rivermax.lic /opt/mellanox/rivermax/; then
            echo ""
            echo -e "${GREEN}Rivermax License successfully moved.${NC}"
            echo ""
        else
            echo ""
            echo -e "${RED}ERROR: Failed to copy Rivermax License.${NC}"
            echo ""
        fi

    elif [[ "$ZIP_FILE" == *"eCapturePro"* ]]; then
        # eCapturePro related commands
        echo "Detected eCapturePro in the filename. Proceeding with eCapturePro installation."

        # Run the eCapturePro installer with the specified arguments
        sudo ./eCaptureProInstaller_0_1_32_Ubuntu_22_04.run in -da -c --al
    else
        echo "Unknown file pattern: $ZIP_FILE. Skipping."
        continue
    fi

    # Return to the parent directory after each iteration
    cd ..
done

echo ""
echo -e "${GREEN}All installation zip files processed!${NC}"
echo ""

# Return to the original directory before removing zip files
cd "$ORIGINAL_DIRECTORY" || { echo -e "${RED}Error: Failed to return to original directory.${NC}"; exit 1; }

echo "Cleaning up installation files..."
echo "Removing zip files from directory."
rm *.zip
echo "Zip files removed."

# Starting HCA Driver, recommended from Installation of the eSDK
echo ""
echo "Starting HCA Driver..."
echo ""

# Attempt to restart the HCA Driver
if sudo /etc/init.d/openibd restart; then
    echo ""
    echo -e "${GREEN}HCA Driver successfully restarted.${NC}"
    echo ""
else
    echo ""
    echo -e "${RED}ERROR: Failed to restart HCA Driver. Please check the logs or configuration.${NC}"
    echo ""
fi

# Indicate to user that script completed successfully
echo ""
echo -e "${GREEN}Emergent Vision Technologies (EVT) software installation completed successfully!${NC}"
echo ""

exit 0
