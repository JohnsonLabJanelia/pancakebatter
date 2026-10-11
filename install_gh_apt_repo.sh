#!/bin/bash

# Add GitHub's apt repository for the GitHub CLI (gh), so the `gh` line in
# packages/apt/sysadmin.txt installs a current release instead of Ubuntu
# jammy's gh 2.4.0. apt_installs.sh runs this before installing the manifests.
#
#   sudo ./install_gh_apt_repo.sh            add the keyring and source, apt update
#   sudo ./install_gh_apt_repo.sh --install  also install/upgrade gh
#   ./install_gh_apt_repo.sh --check         report the repo and gh state (no root)
#
# The downloaded keyring is accepted only if every primary key in it is one of
# GH_KEY_FINGERPRINTS. When GitHub rotates its signing key the script refuses;
# check the new fingerprint against https://github.com/cli/cli/blob/trunk/docs/install_linux.md
# and add it to the list.

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

GH_KEYRING_URL="https://cli.github.com/packages/githubcli-archive-keyring.gpg"
GH_KEYRING="/etc/apt/keyrings/githubcli-archive-keyring.gpg"
GH_SOURCES_LIST="/etc/apt/sources.list.d/github-cli.list"
GH_KEY_FINGERPRINTS=(
    2C6106201985B60E6C7AC87323F3D4EA75716059   # GitHub CLI, 2022-09 (expired 2026-09)
    7F38BBB59D064DBCB3D84D725612B36462313325   # GitHub CLI, 2026-04
)

error_exit() {
    echo -e "${RED}Error: $1${NC}" >&2
    exit 1
}

primary_fingerprints() {
    gpg --show-keys --with-colons "$1" 2>/dev/null \
        | awk -F: '$1 == "pub" {want = 1; next} $1 == "fpr" && want {print $10; want = 0}'
}

keyring_trusted() {
    local keyring="$1" fpr known found=0
    while read -r fpr; do
        [ -n "$fpr" ] || continue
        found=1
        known=0
        for k in "${GH_KEY_FINGERPRINTS[@]}"; do
            [ "$fpr" = "$k" ] && known=1
        done
        if [ "$known" -ne 1 ]; then
            echo -e "${RED}Unknown key in GitHub CLI keyring: ${fpr}${NC}" >&2
            return 1
        fi
    done < <(primary_fingerprints "$keyring")
    [ "$found" -eq 1 ]
}

sources_line() {
    echo "deb [arch=$(dpkg --print-architecture) signed-by=${GH_KEYRING}] https://cli.github.com/packages stable main"
}

download() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$2" "$1"
    else
        error_exit "Neither curl nor wget is available."
    fi
}

check() {
    local rc=0
    if [ -f "$GH_KEYRING" ] && keyring_trusted "$GH_KEYRING"; then
        echo -e "${GREEN}keyring: ${GH_KEYRING} (known keys)${NC}"
    else
        echo -e "${YELLOW}keyring: missing or not trusted (${GH_KEYRING})${NC}"
        rc=1
    fi
    if [ -f "$GH_SOURCES_LIST" ] && grep -qxF "$(sources_line)" "$GH_SOURCES_LIST"; then
        echo -e "${GREEN}source: ${GH_SOURCES_LIST}${NC}"
    else
        echo -e "${YELLOW}source: missing or different (${GH_SOURCES_LIST})${NC}"
        rc=1
    fi
    if command -v gh >/dev/null 2>&1; then
        echo "gh: $(gh --version | head -1)"
    else
        echo "gh: not installed"
    fi
    return "$rc"
}

case "${1:-}" in
    --check) check; exit $? ;;
    --install|"") ;;
    *) error_exit "Usage: $0 [--install | --check]" ;;
esac

if [ "$EUID" -ne 0 ]; then
    error_exit "This script must be run as root. Please use sudo."
fi
command -v gpg >/dev/null 2>&1 || error_exit "gpg is required to check the keyring."

tmp_keyring="$(mktemp)"
trap 'rm -f "$tmp_keyring"' EXIT

echo -e "${YELLOW}Downloading the GitHub CLI apt keyring...${NC}"
download "$GH_KEYRING_URL" "$tmp_keyring" || error_exit "Failed to download ${GH_KEYRING_URL}."
keyring_trusted "$tmp_keyring" || error_exit "Downloaded keyring has an unknown or no signing key; see the header of $0."

install -d -m 0755 /etc/apt/keyrings || error_exit "Failed to create /etc/apt/keyrings."
install -m 0644 "$tmp_keyring" "$GH_KEYRING" || error_exit "Failed to install ${GH_KEYRING}."
sources_line > "$GH_SOURCES_LIST" || error_exit "Failed to write ${GH_SOURCES_LIST}."
chmod 0644 "$GH_SOURCES_LIST"

apt-get update || error_exit "apt-get update failed."

if [ "${1:-}" = "--install" ]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y gh || error_exit "Failed to install gh."
fi

echo -e "${GREEN}GitHub CLI apt repository configured.${NC}"
check || true
