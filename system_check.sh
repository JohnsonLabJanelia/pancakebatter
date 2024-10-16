#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color (Reset)

# Path to the Python script
PYTHON_SCRIPT="system_check.py"

# Check if the script is being run with sudo
if [ "$EUID" -ne 0 ]; then
    echo ""
    echo -e "${RED}ERROR: Please run this script as sudo.${NC}"
    echo -e "This script requires root permissions to check NIC Ports and IPs.${NC}"
    echo ""
    exit 1
fi

# Check if the Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo -e "${RED}Python script $PYTHON_SCRIPT not found!${NC}"
    exit 1
fi

# Run the Python script
python3 "$PYTHON_SCRIPT" --verbose --log

# Check the exit status of the Python script
exit_code=$?

echo ""

case $exit_code in
    0)
        echo -e "${GREEN}System check passed!${NC}"
        ;;
    1)
        echo -e "${RED}SYSTEM CHECK FAILED: GPU mismatch detected!${NC}"
        ;;
    2)
        echo -e "${RED}SYSTEM CHECK FAILED: System information mismatch detected!${NC}"
        ;;
    3)
        echo -e "${RED}SYSTEM CHECK FAILED: NIC mismatch detected!${NC}"
        ;;
    4)
        echo -e "${RED}SYSTEM CHECK FAILED: NIC port mismatch detected!${NC}"
        ;;
    5)
        echo -e "${RED}SYSTEM CHECK FAILED: Camera configuration mismatch detected!${NC}"
        ;;
    *)
        echo -e "${YELLOW}SYSTEM CHECK FAILED: Unknown error (Exit code: $exit_code).${NC}"
        ;;
esac

echo ""

# Get the original user (the one who ran the script with sudo)
ORIGINAL_USER=$SUDO_USER

# Change ownership of files owned by root to the original user
echo "Changing ownership of files owned by root to $ORIGINAL_USER"
echo ""
find logs -user root -exec chown "$ORIGINAL_USER":"$ORIGINAL_USER" {} \;
# If successful, print a success message
if [ $? -eq 0 ]; then
    echo -e "${GREEN}Ownership changed successfully!${NC}"
else
    echo -e "${RED}Failed to change ownership!${NC}"
fi
echo ""

# Pass the same exit code back to the caller
exit $exit_code