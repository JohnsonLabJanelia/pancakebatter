#!/bin/bash

# ANSI color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color (Reset)

# Path to the Python script
PYTHON_SCRIPT="gpu_check.py"

# Check if the Python script exists
if [ ! -f "$PYTHON_SCRIPT" ]; then
    echo -e "${RED}Python script $PYTHON_SCRIPT not found!${NC}"
    exit 1
fi

# Run the Python script
python3 "$PYTHON_SCRIPT" --verbose

# Check the exit status of the Python script
exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo -e "${GREEN}System check passed!${NC}"
elif [ $exit_code -eq 1 ]; then
    echo -e "${RED}SYSTEM CHECK FAILED: GPU mismatch detected!${NC}"
elif [ $exit_code -eq 2 ]; then
    echo -e "${RED}SYSTEM CHECK FAILED: System information mismatch detected!${NC}"
else
    echo -e "${YELLOW}SYSTEM CHECK FAILED: Unknown error.${NC}"
fi

echo ""

# Pass the same exit code back to the caller
exit $exit_code
