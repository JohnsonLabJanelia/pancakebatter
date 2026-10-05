#!/usr/bin/env bash
# Install (or remove) ptp4l.service and phc2sys.service so PTP for the camera ports starts at boot
# and restarts on failure, instead of the hand-started "sudo -b ptp4l ... / sudo -b phc2sys ...".
#
#   sudo ./install_ptp_units.sh [--replace-running]   # render, install, enable --now
#   sudo ./install_ptp_units.sh --uninstall
#
# Interfaces: every NIC in hosts/<host>/config.yml with role camera and expected_link true.
# /etc/ptp4l.conf: installed from configs/ptp4l.conf if absent; an existing, different file is
# left alone and reported. Manually started ptp4l/phc2sys processes are reported; with
# --replace-running they are killed first (refused while Orange is recording). Re-run after
# changing camera ports or isolated cores.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/lib/host_config.sh"
UNIT_DIR=/etc/systemd/system
REPLACE=0

[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now phc2sys.service ptp4l.service 2>/dev/null || true
    rm -f "$UNIT_DIR/ptp4l.service" "$UNIT_DIR/phc2sys.service"
    systemctl daemon-reload
    echo "removed ptp4l.service and phc2sys.service (/etc/ptp4l.conf kept)"
    exit 0
fi
while [[ $# -gt 0 ]]; do
    case "$1" in
        --replace-running) REPLACE=1 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done
[[ -f "$HOST_CONFIG_FILE" ]] || { echo "missing $HOST_CONFIG_FILE" >&2; exit 1; }

IFACES="$(/usr/bin/python3 - "$HOST_CONFIG_FILE" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1])) or {}
names = [n for e in cfg.get("nics") or [] for n, v in (e or {}).items()
         if (v or {}).get("role") == "camera" and (v or {}).get("expected_link")]
print(" ".join(names))
PY
)"
[[ -n "$IFACES" ]] || { echo "no camera NICs with expected_link: true in $HOST_CONFIG_FILE" >&2; exit 1; }
AFFINITY="$(/usr/bin/python3 "$HERE/rig_health_check.py" --print-affinity)"
IFACE_ARGS=""; DEVICE_UNITS=""
for i in $IFACES; do
    IFACE_ARGS+="-i $i "
    DEVICE_UNITS+="sys-subsystem-net-devices-$(systemd-escape "$i").device "
done

# Hand-started daemons (anything not already inside our units)
MANUAL=""
for name in ptp4l phc2sys; do
    for pid in $(pgrep -x "$name" || true); do
        grep -q "/$name.service" "/proc/$pid/cgroup" 2>/dev/null || MANUAL+="$pid "
    done
done
if [[ -n "$MANUAL" ]]; then
    if (( REPLACE )); then
        if pgrep -x orange >/dev/null; then
            echo "Orange is recording; not killing the running PTP daemons. Stop it first." >&2; exit 3
        fi
        echo "stopping hand-started ptp4l/phc2sys: $MANUAL"
        kill $MANUAL; sleep 2
    else
        echo "NOTE: hand-started ptp4l/phc2sys running (pids: $MANUAL). The units are installed and enabled"
        echo "      for boot but NOT started now; re-run with --replace-running to take over."
    fi
fi

if [[ ! -e /etc/ptp4l.conf ]]; then
    install -o root -g root -m 0644 "$HERE/configs/ptp4l.conf" /etc/ptp4l.conf
    echo "installed /etc/ptp4l.conf from configs/ptp4l.conf"
elif ! cmp -s /etc/ptp4l.conf "$HERE/configs/ptp4l.conf"; then
    echo "NOTE: /etc/ptp4l.conf differs from configs/ptp4l.conf; leaving the installed file alone:"
    diff /etc/ptp4l.conf "$HERE/configs/ptp4l.conf" || true
fi

sed -e "s|@REPO@|$HERE|g" -e "s|@INTERFACE_ARGS@|${IFACE_ARGS% }|g" -e "s|@DEVICE_UNITS@|${DEVICE_UNITS% }|g" \
    -e "s|@CPUAFFINITY@|$AFFINITY|g" "$HERE/systemd/ptp4l.service.in" > "$UNIT_DIR/ptp4l.service"
sed -e "s|@REPO@|$HERE|g" -e "s|@CPUAFFINITY@|$AFFINITY|g" "$HERE/systemd/phc2sys.service.in" > "$UNIT_DIR/phc2sys.service"
chmod 0644 "$UNIT_DIR/ptp4l.service" "$UNIT_DIR/phc2sys.service"
systemctl daemon-reload
if [[ -n "$MANUAL" && $REPLACE -eq 0 ]]; then
    systemctl enable ptp4l.service phc2sys.service
else
    systemctl enable --now ptp4l.service phc2sys.service
fi

echo "ptp4l.service: $IFACE_ARGS(CPUs $AFFINITY); phc2sys.service follows it (PartOf)"
echo "status:  systemctl status ptp4l phc2sys --no-pager"
echo "logs:    journalctl -u ptp4l -u phc2sys -f"
