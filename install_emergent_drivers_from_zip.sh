#!/bin/bash

# This script is used to install the Emergent Vision Technologies (EVT) eSDK and its drivers for Mellanox
# Author: Jeremy Delahanty, ChatGPT4o 20240930

# This assumes you have downloaded the appropriate eSDK version from Emergent Vision Technologies (EVT) and Mellanox
# and have them in the same directory as this script. You should run as sudo because doing things with
# EVT/installing requires root permission

# Find all zip files in the current directory
ZIP_FILES=(*.zip)

# Check for the number of zip files found
if [ ${#ZIP_FILES[@]} -eq 0 ]; then # If there are no zip files found, tell the user
    echo "Error: No .zip files found in the current directory."
    exit 1
elif [ ${#ZIP_FILES[@]} -ne 1 ]; then # If there is more than file found, also tell the user
    echo "Error: More than one .zip file found. Please ensure only one zip file is present."
    exit 1
fi

# Define the zip file and target directory
ZIP_FILE="${ZIP_FILES[0]}"
TARGET_DIR="${ZIP_FILE%.zip}"  # Remove the .zip extension for the directory name

# Create the target directory
mkdir -p "$TARGET_DIR"

# Unzip the zip file into the target directory
unzip "$ZIP_FILE" -d "$TARGET_DIR"

# Install eSDK and its drivers for Mellanox and EVT
# Change into the target directory
cd "$TARGET_DIR" || { echo "Error: Failed to change directory to $TARGET_DIR"; exit 1; }

# Print the current directory to confirm for user
echo "Changed directory to $(pwd)"

# We'll now install the .deb file
# TODO: What is difference between .deb and other install file? Just drivers?
# Find the .deb package
DEB_PACKAGE=(*.deb)

# Check if a .deb package is found
if [ ${#DEB_PACKAGE[@]} -eq 0 ]; then
    echo "Error: No .deb package found in $TARGET_DIR."
    exit 1
fi

# Install the .deb package with a rollback mechanism
echo "Installing package: ${DEB_PACKAGE[0]}"
sudo dpkg -i "${DEB_PACKAGE[0]}"

# Use install_eSdk.sh twice to install the relevant drivers: Mellanox and EVT
# These are the two drivers needed for communicating with each type of
# connector on either the switch, server, or camera. The lab does not use any
# other connectors.
sudo ./install_eSdk.sh -i Mellanox && sudo ./install_eSdk.sh -i EVT
