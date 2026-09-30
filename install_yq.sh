#!/bin/bash

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

YQ_DOWNLOAD_URL="${YQ_DOWNLOAD_URL:-https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64}"
YQ_INSTALL_PATH="${YQ_INSTALL_PATH:-/usr/local/bin/yq}"

error_exit() {
    echo -e "${RED}Error: $1${NC}" >&2
    exit 1
}

compatible_yq() {
    local yq_bin="${1:-yq}"
    printf '{}\n' | "$yq_bin" e '.test' - >/dev/null 2>&1
}

download_yq_binary() {
    local output_path="$1"

    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$YQ_DOWNLOAD_URL" -o "$output_path"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$output_path" "$YQ_DOWNLOAD_URL"
    else
        error_exit "Neither curl nor wget is available to download yq."
    fi
}

if [ "$EUID" -ne 0 ]; then
    error_exit "This script must be run as root. Please use sudo."
fi

if command -v yq >/dev/null 2>&1 && compatible_yq yq; then
    echo -e "${GREEN}Compatible yq already installed: $(yq --version)${NC}"
    exit 0
fi

echo -e "${YELLOW}Installing mikefarah/yq to ${YQ_INSTALL_PATH}...${NC}"
tmp_yq="$(mktemp)"
trap 'rm -f "$tmp_yq"' EXIT

download_yq_binary "$tmp_yq" || error_exit "Failed to download yq."
chmod +x "$tmp_yq" || error_exit "Failed to mark downloaded yq binary as executable."

if ! compatible_yq "$tmp_yq"; then
    error_exit "Downloaded yq binary does not support 'yq e' syntax."
fi

install -m 0755 "$tmp_yq" "$YQ_INSTALL_PATH" || error_exit "Failed to install yq to ${YQ_INSTALL_PATH}."

if ! compatible_yq "$YQ_INSTALL_PATH"; then
    error_exit "Installed yq binary failed compatibility verification."
fi

echo -e "${GREEN}Installed compatible yq: $("$YQ_INSTALL_PATH" --version)${NC}"
