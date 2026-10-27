#!/usr/bin/env bash
# Install (or remove) the root NVMe SMART snapshot the rig health check reads: a root-owned copy of
# libexec/nvme_smart_snapshot.py at /usr/local/libexec/pancakebatter/nvme_smart_snapshot, run every
# INTERVAL (default 5min) by nvme-smart-snapshot.timer, writing only
# /var/lib/pancakebatter-nvme/smart.json (world-readable).
#
#   sudo ./install_nvme_smart_snapshot.sh [--interval 5min]
#   sudo ./install_nvme_smart_snapshot.sh --uninstall
#
# Re-run after editing libexec/nvme_smart_snapshot.py (the installed copy does not follow the
# checkout) or changing isolated_cores. See docs/rig_health_monitor.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib/host_config.sh"
UNIT_DIR=/etc/systemd/system
DEST_DIR=/usr/local/libexec/pancakebatter
DEST="$DEST_DIR/nvme_smart_snapshot"
INTERVAL=5min

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now nvme-smart-snapshot.timer 2>/dev/null || true
    rm -f "$UNIT_DIR/nvme-smart-snapshot.service" "$UNIT_DIR/nvme-smart-snapshot.timer" "$DEST"
    systemctl daemon-reload
    echo "removed nvme-smart-snapshot.service, .timer and $DEST (/var/lib/pancakebatter-nvme kept)"
    exit 0
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --interval) INTERVAL="${2:?--interval needs a value like 5min}"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[[ "$INTERVAL" =~ ^[0-9]+(s|sec|min|m|h|hr)$ ]] || { echo "invalid interval: $INTERVAL" >&2; exit 1; }
[[ -x /usr/sbin/nvme ]] || { echo "/usr/sbin/nvme missing (apt install nvme-cli)" >&2; exit 1; }
[[ -f "$HOST_CONFIG_FILE" ]] || { echo "missing $HOST_CONFIG_FILE" >&2; exit 1; }

AFFINITY="$(/usr/bin/python3 "$HERE/rig_health_check.py" --print-affinity)"
[[ -n "$AFFINITY" ]] || { echo "could not compute CPU affinity" >&2; exit 1; }

install -d -o root -g root -m 0755 "$DEST_DIR"
install -o root -g root -m 0755 "$HERE/libexec/nvme_smart_snapshot.py" "$DEST"
sed -e "s|@REPO@|$HERE|g" -e "s|@CPUAFFINITY@|$AFFINITY|g" \
    "$HERE/systemd/nvme-smart-snapshot.service.in" > "$UNIT_DIR/nvme-smart-snapshot.service"
sed -e "s|@INTERVAL@|$INTERVAL|g" "$HERE/systemd/nvme-smart-snapshot.timer.in" > "$UNIT_DIR/nvme-smart-snapshot.timer"
chmod 0644 "$UNIT_DIR/nvme-smart-snapshot.service" "$UNIT_DIR/nvme-smart-snapshot.timer"

systemctl daemon-reload
# One snapshot now, so the health check has data at once (a smart-log failure is reported, not fatal).
systemctl start nvme-smart-snapshot.service || echo "first snapshot reported a problem: journalctl -u nvme-smart-snapshot" >&2
systemctl enable --now nvme-smart-snapshot.timer
echo "installed $DEST; snapshot every $INTERVAL to /var/lib/pancakebatter-nvme/smart.json"
