#!/usr/bin/env bash
# Install (or remove) clock-watch.service: libexec/clock_watch.c, built here, checking once a second that
# the kernel clock works on one non-isolated CPU per L3 group, and on every non-isolated CPU while any is
# bad. rig_health_check.py reads its status (/run/clock-watch/status.json) for the clock_steps check and
# the --preflight gate. Why: the 2026-11-11 chiplet clock fault in docs/time_and_clocks.md.
#
#   sudo ./install_clock_watch.sh [--user NAME]
#   sudo ./install_clock_watch.sh --uninstall
#
# Re-run after changing kernel_tuning.isolated_cores (the unit embeds the CPU lists) or clock_watch.c.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib/host_config.sh"
UNIT=/etc/systemd/system/clock-watch.service
BINARY=/usr/local/libexec/pancakebatter/clock_watch
TARGET_USER="${SUDO_USER:-}"

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now clock-watch.service 2>/dev/null || true
    rm -f "$UNIT" "$BINARY"
    systemctl daemon-reload
    echo "removed clock-watch.service and $BINARY (/var/lib/clock-watch kept)"
    exit 0
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --user) TARGET_USER="${2:?--user needs a name}"; shift ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]] || { echo "pass --user NAME" >&2; exit 1; }
[[ "$TARGET_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "invalid user name" >&2; exit 1; }
id "$TARGET_USER" >/dev/null
[[ -f "$HOST_CONFIG_FILE" ]] || { echo "missing $HOST_CONFIG_FILE" >&2; exit 1; }

read -r REF PROBE SWEEP < <(/usr/bin/python3 "$HERE/rig_health_check.py" --print-clock-watch-cpus)
AFFINITY="$(/usr/bin/python3 "$HERE/rig_health_check.py" --print-affinity)"
for v in REF PROBE SWEEP AFFINITY; do
    [[ -n "${!v}" && "${!v}" =~ ^[0-9,\ -]+$ ]] || { echo "could not compute $v" >&2; exit 1; }
done

BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
cc -O2 -Wall -Werror -o "$BUILD/clock_watch" "$HERE/libexec/clock_watch.c"
install -D -o root -g root -m 0755 "$BUILD/clock_watch" "$BINARY"

sed -e "s|@REPO@|$HERE|g" -e "s|@USER@|$TARGET_USER|g" -e "s|@BINARY@|$BINARY|g" -e "s|@REF@|$REF|g" \
    -e "s|@PROBE@|$PROBE|g" -e "s|@SWEEP@|$SWEEP|g" -e "s|@AFFINITY@|$AFFINITY|g" \
    "$HERE/systemd/clock-watch.service.in" > "$UNIT"
chmod 0644 "$UNIT"

systemctl daemon-reload
systemctl enable clock-watch.service
systemctl restart clock-watch.service

echo "installed clock-watch.service as $TARGET_USER: reference CPU $REF, probes $PROBE, sweep $SWEEP"
echo "status:   cat /run/clock-watch/status.json; systemctl status clock-watch"
echo "history:  /var/lib/clock-watch/events.jsonl (state changes and a summary each minute)"
echo "boot log: /var/lib/clock-watch/dmesg-boot-<boot id>.txt"
