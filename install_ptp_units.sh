#!/usr/bin/env bash
# Install (or remove) ptp4l.service and phc2sys.service so PTP for the camera ports starts at boot
# and restarts on failure, instead of the hand-started "sudo -b ptp4l ... / sudo -b phc2sys ...".
#
#   sudo ./install_ptp_units.sh [--replace-running] [--restart]   # render, install, enable --now
#   sudo ./install_ptp_units.sh --uninstall
#
# --restart: also restart a running ptp4l.service (phc2sys follows, PartOf) so changed units take
# effect; refused while Orange runs, because the cameras briefly lose PTP lock.
# libexec/ptp4l_gm_settings.sh is installed root-owned to $GM_SETTINGS and run by ptp4l.service
# after each start to declare the UTC offset valid (docs/ptp_services.md).
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
GM_SETTINGS=/usr/local/libexec/pancakebatter/ptp4l_gm_settings
REPLACE=0
RESTART=0


# Not root? Re-run through the passwordless helper if install_admin_helper.sh has set it up.
ADMIN_HELPER=/usr/local/libexec/pancakebatter/admin
if [[ $EUID -ne 0 && -x "$ADMIN_HELPER" ]] && sudo -n -l "$ADMIN_HELPER" >/dev/null 2>&1; then
    exec sudo -n "$ADMIN_HELPER" install-ptp-units "$@"
fi
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

if [[ "${1:-}" == "--uninstall" ]]; then
    systemctl disable --now phc2sys.service ptp4l.service 2>/dev/null || true
    rm -f "$UNIT_DIR/ptp4l.service" "$UNIT_DIR/phc2sys.service" "$GM_SETTINGS"
    systemctl daemon-reload
    echo "removed ptp4l.service and phc2sys.service (/etc/ptp4l.conf kept)"
    exit 0
fi
while [[ $# -gt 0 ]]; do
    case "$1" in
        --replace-running) REPLACE=1 ;;
        --restart) RESTART=1 ;;
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

WAS_ACTIVE=0
systemctl is-active --quiet ptp4l.service && WAS_ACTIVE=1
install -d -o root -g root -m 0755 "$(dirname "$GM_SETTINGS")"
install -o root -g root -m 0755 "$HERE/libexec/ptp4l_gm_settings.sh" "$GM_SETTINGS"
sed -e "s|@REPO@|$HERE|g" -e "s|@INTERFACE_ARGS@|${IFACE_ARGS% }|g" -e "s|@DEVICE_UNITS@|${DEVICE_UNITS% }|g" \
    -e "s|@CPUAFFINITY@|$AFFINITY|g" -e "s|@GM_SETTINGS@|$GM_SETTINGS|g" \
    "$HERE/systemd/ptp4l.service.in" > "$UNIT_DIR/ptp4l.service"
sed -e "s|@REPO@|$HERE|g" -e "s|@CPUAFFINITY@|$AFFINITY|g" "$HERE/systemd/phc2sys.service.in" > "$UNIT_DIR/phc2sys.service"
chmod 0644 "$UNIT_DIR/ptp4l.service" "$UNIT_DIR/phc2sys.service"
systemctl daemon-reload
if [[ -n "$MANUAL" && $REPLACE -eq 0 ]]; then
    systemctl enable ptp4l.service phc2sys.service
else
    systemctl enable --now ptp4l.service phc2sys.service
    if (( RESTART )); then
        if pgrep -x orange >/dev/null; then
            echo "Orange is running; not restarting ptp4l (the cameras would lose PTP lock). Stop it first." >&2
            exit 3
        fi
        systemctl restart ptp4l.service phc2sys.service
        echo "restarted ptp4l.service and phc2sys.service"
    elif (( WAS_ACTIVE )); then
        echo "NOTE: ptp4l.service was already running and keeps its old settings until restarted"
        echo "      (re-run with --restart when no recording or stream is active)."
    fi
fi

echo "ptp4l.service: $IFACE_ARGS(CPUs $AFFINITY); phc2sys.service follows it (PartOf)"
echo "status:  systemctl status ptp4l phc2sys --no-pager"
echo "logs:    journalctl -u ptp4l -u phc2sys -f"
