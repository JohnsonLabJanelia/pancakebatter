#!/usr/bin/env bash
# Install (or remove) the rig health timer: rig_health_check.py every INTERVAL (default 5min) as the
# invoking user, reniced, IO/CPU idle class, pinned to the cores that are NOT in
# kernel_tuning.isolated_cores of hosts/<host>/config.yml, writing only to /var/lib/rig-health.
#
#   sudo ./install_rig_health_timer.sh [--user NAME] [--interval 5min]
#   sudo ./install_rig_health_timer.sh --uninstall
#
# Re-run after moving the checkout or changing isolated_cores (the unit embeds both).
# Prefer cron? The equivalent line is in docs/rig_health_monitor.md.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib/host_config.sh"
UNIT_DIR=/etc/systemd/system
ENV_FILE=/etc/default/rig-health
INTERVAL=5min
TARGET_USER="${SUDO_USER:-}"

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now rig-health.timer 2>/dev/null || true
    rm -f "$UNIT_DIR/rig-health.service" "$UNIT_DIR/rig-health.timer"
    systemctl daemon-reload
    echo "removed rig-health.service and rig-health.timer ($ENV_FILE and /var/lib/rig-health kept)"
    exit 0
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) TARGET_USER="${2:?--user needs a name}"; shift ;;
        --interval) INTERVAL="${2:?--interval needs a value like 5min}"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]] || { echo "pass --user NAME" >&2; exit 1; }
[[ "$TARGET_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "invalid user name" >&2; exit 1; }
[[ "$INTERVAL" =~ ^[0-9]+(s|sec|min|m|h|hr)$ ]] || { echo "invalid interval: $INTERVAL" >&2; exit 1; }
id "$TARGET_USER" >/dev/null
[[ -f "$HOST_CONFIG_FILE" ]] || { echo "missing $HOST_CONFIG_FILE" >&2; exit 1; }

AFFINITY="$(/usr/bin/python3 "$HERE/rig_health_check.py" --print-affinity)"
[[ -n "$AFFINITY" ]] || { echo "could not compute CPU affinity" >&2; exit 1; }

sed -e "s|@REPO@|$HERE|g" -e "s|@USER@|$TARGET_USER|g" -e "s|@CPUAFFINITY@|$AFFINITY|g" \
    "$HERE/systemd/rig-health.service.in" > "$UNIT_DIR/rig-health.service"
sed -e "s|@INTERVAL@|$INTERVAL|g" "$HERE/systemd/rig-health.timer.in" > "$UNIT_DIR/rig-health.timer"
chmod 0644 "$UNIT_DIR/rig-health.service" "$UNIT_DIR/rig-health.timer"
[[ -e "$ENV_FILE" ]] || install -o root -g root -m 0644 "$HERE/configs/rig-health.default" "$ENV_FILE"

systemctl daemon-reload
systemctl enable --now rig-health.timer
systemctl start rig-health.service || true   # first run now; exit 1/2 are findings, not failures

echo "installed rig-health.timer (every $INTERVAL) running as $TARGET_USER on CPUs $AFFINITY"
echo "mail:     edit $ENV_FILE (RIG_HEALTH_EMAIL) and run: sudo systemctl restart rig-health.service"
echo "status:   systemctl list-timers rig-health.timer; journalctl -u rig-health.service -n 40"
echo "latest:   cat /var/lib/rig-health/last.json"
