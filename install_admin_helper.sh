#!/usr/bin/env bash
# Install (or remove) the passwordless helper for pancakebatter's installers, so
#   ./install_host_config.sh   ./install_ptp_units.sh   ./install_rig_health_timer.sh
# re-run themselves as root without a sudo password when invoked by the allowed user.
#
#   sudo ./install_admin_helper.sh [--user NAME]     # default user: $SUDO_USER
#   sudo ./install_admin_helper.sh --uninstall
#
# Installs a root-owned copy of libexec/pancakebatter_admin (with this checkout's path baked in) to
# /usr/local/libexec/pancakebatter/admin and a sudoers.d rule allowing exactly that path for one user.
# Security note: the helper runs the checkout's installer scripts as root, so this is equivalent to
# passwordless root for whoever can edit the checkout. Fine while the checkout owner is the machine's
# sudoer; uninstall before the checkout becomes shared.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR=/usr/local/libexec/pancakebatter
DEST="$DEST_DIR/admin"
RULE=/etc/sudoers.d/pancakebatter-admin

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
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
sed "s|@REPO@|$HERE|g" "$HERE/libexec/pancakebatter_admin" > "$TMP"
install -o root -g root -m 0755 "$TMP" "$DEST"

echo "$TARGET_USER ALL=(root) NOPASSWD: $DEST" > "$TMP"
visudo -cf "$TMP" >/dev/null || { echo "generated sudoers rule failed validation; nothing installed" >&2; exit 1; }
install -o root -g root -m 0440 "$TMP" "$RULE"

echo "installed $DEST (checkout $HERE) and $RULE for user $TARGET_USER"
echo "now: ./install_host_config.sh and ./install_ptp_units.sh run without a password for $TARGET_USER"
