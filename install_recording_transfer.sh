#!/usr/bin/env bash
# Install, verify, list or remove released recording-transfer sealer versions under
# paths.recording_transfer_root (/opt/recording-transfer). See recording_transfer_install.py.
#
#   ./install_recording_transfer.sh install 3.0.0 [--wheel PATH]
#   ./install_recording_transfer.sh check | list
#   ./install_recording_transfer.sh uninstall 3.0.0
#
# install/uninstall need root; with the admin helper installed (install_admin_helper.sh) they
# re-run through it without a password. check and list run as anyone.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "${1:-}" in
    check|list) exec /usr/bin/python3 "$HERE/recording_transfer_install.py" "$@" ;;
esac

# Not root? Re-run through the passwordless helper if install_admin_helper.sh has set it up.
ADMIN_HELPER=/usr/local/libexec/pancakebatter/admin
if [[ $EUID -ne 0 && -x "$ADMIN_HELPER" ]] && sudo -n -l "$ADMIN_HELPER" >/dev/null 2>&1; then
    exec sudo -n "$ADMIN_HELPER" install-recording-transfer "$@"
fi
[[ $EUID -eq 0 ]] || { echo "run with sudo (or install the admin helper)" >&2; exit 1; }
exec /usr/bin/python3 -E -s "$HERE/recording_transfer_install.py" "$@"
