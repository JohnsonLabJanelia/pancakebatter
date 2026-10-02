#!/usr/bin/env bash
# Install (or remove) the read-only root probe so capture_inventory.py can read
# transceiver EEPROM and NIC VPD without a sudo password.
#
#   sudo ./install_root_probe.sh [--user NAME]     # default user: $SUDO_USER
#   sudo ./install_root_probe.sh --uninstall
#
# Installs a root-owned copy to /usr/local/libexec/pancakebatter/ and a sudoers.d rule
# allowing that exact path, with no arguments, for one user. Nothing else changes.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/libexec/pancakebatter_root_probe.py"
DEST_DIR=/usr/local/libexec/pancakebatter
DEST="$DEST_DIR/root_probe.py"
RULE=/etc/sudoers.d/pancakebatter-probe

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$RULE" "$DEST"
    rmdir "$DEST_DIR" 2>/dev/null || true
    echo "removed $RULE and $DEST"
    exit 0
fi

TARGET_USER="${SUDO_USER:-}"
if [[ "${1:-}" == "--user" ]]; then TARGET_USER="${2:?--user needs a name}"; fi
[[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]] || { echo "pass --user NAME" >&2; exit 1; }
[[ "$TARGET_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "invalid user name" >&2; exit 1; }
id "$TARGET_USER" >/dev/null

install -d -o root -g root -m 0755 "$DEST_DIR"
install -o root -g root -m 0755 "$SRC" "$DEST"

# Trailing "" = the command may be run with no arguments only.
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
echo "$TARGET_USER ALL=(root) NOPASSWD: $DEST \"\"" > "$TMP"
visudo -cf "$TMP" >/dev/null || { echo "generated sudoers rule failed validation; nothing installed" >&2; exit 1; }
install -o root -g root -m 0440 "$TMP" "$RULE"

echo "installed $DEST and $RULE for user $TARGET_USER"
echo "check: sudo -n $DEST | head -c 200"
