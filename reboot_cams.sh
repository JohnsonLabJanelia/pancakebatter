#!/usr/bin/env bash
# Power-cycle the camera PDU outlets and verify the cameras and their EF lens
# mounts came back in the configured state.
#
# Why both halves: the PDU cut is the only reset that removes power from the
# camera, the EF mount and the lens (the SDK's EVT_ForceReboot only restarts
# camera firmware). The SDK is the right tool for the "after": wait for
# discovery, open each camera without streaming, confirm the mount and lens
# are present, and apply + verify the configured focus/iris through
# IrisCurrent/FocusCurrent (the EF mount drops writes issued while LensBusy,
# and the commanded registers cannot show that).
#
# Usage: ./reboot_cams.sh [--no-apply] [--outlet <id|all>] [--wait-seconds <s>]
#   ORANGE_CAMERA_READY_ROOT  tree holding targets/release/evt_camera_ready
#                          (default /home/jeremy/orange-integration-20260921)
#   ORANGE_CAMERA_CONFIG   camera config folder with <serial>.json
#                          (default ~/orange_data/config/local/100_cam4_ptp_fourcam)
#   PDU_HOST               default 192.168.20.177
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PDU_HOST="${PDU_HOST:-192.168.20.177}"
# ORANGE_ROOT is a system-wide variable on this host (/opt/orange), so use a
# dedicated one for the readiness tool.
ORANGE_CAMERA_READY_ROOT="${ORANGE_CAMERA_READY_ROOT:-/home/jeremy/orange-integration-20260921}"
ORANGE_CAMERA_CONFIG="${ORANGE_CAMERA_CONFIG:-$HOME/orange_data/config/local/100_cam4_ptp_fourcam}"
READY_BIN="$ORANGE_CAMERA_READY_ROOT/targets/release/evt_camera_ready"
OUTLET="all"
WAIT_SECONDS=150
APPLY=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-apply) APPLY=0 ;;
    --outlet) OUTLET="$2"; shift ;;
    --wait-seconds) WAIT_SECONDS="$2"; shift ;;
    -h|--help) sed -n 2,20p "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done
[[ -x "$READY_BIN" ]] || { echo "missing $READY_BIN (build target evt_camera_ready)" >&2; exit 2; }

# Serials come from the PDU outlet descriptions in system_config.yml ("SN: 2010093").
SERIALS="$(python3 - "$HERE/system_config.yml" "$PDU_HOST" <<'PY'
import re, sys, yaml
cfg = yaml.safe_load(open(sys.argv[1]))
pdus = cfg.get("pdus") or cfg.get("pdu") or []
if isinstance(pdus, dict): pdus = [pdus]
serials = []
for pdu in pdus:
    if str(pdu.get("ip_address")) != sys.argv[2]: continue
    for outlet in (pdu.get("outlets") or {}).values():
        m = re.search(r"SN:\s*(\d+)", str((outlet or {}).get("description") or ""))
        if m: serials.append(m.group(1))
print(",".join(sorted(serials)))
PY
)"
[[ -n "$SERIALS" ]] || { echo "no camera serials found in system_config.yml for PDU $PDU_HOST" >&2; exit 2; }

if pgrep -f "targets/release/orange" >/dev/null; then
  echo "Orange is running; stop it before power-cycling the cameras." >&2
  exit 3
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$HERE/logs/camera_power_cycle"; mkdir -p "$LOG_DIR"
REPORT="$LOG_DIR/camera_ready_${STAMP}.json"

echo "[reboot_cams] PDU $PDU_HOST outlet=$OUTLET reboot ($STAMP)"
(cd "$HERE" && ./pdu.py --host "$PDU_HOST" --action reboot --outlet "$OUTLET" --verify)

echo "[reboot_cams] waiting for cameras $SERIALS (up to ${WAIT_SECONDS}s), then checking lens mounts"
ARGS=(--serials "$SERIALS" --wait-seconds "$WAIT_SECONDS" --config-dir "$ORANGE_CAMERA_CONFIG" --json "$REPORT")
(( APPLY )) && ARGS+=(--apply-lens)
if "$READY_BIN" "${ARGS[@]}"; then
  echo "[reboot_cams] cameras ready; report $REPORT"
else
  echo "[reboot_cams] CAMERA/LENS CHECK FAILED; report $REPORT" >&2
  exit 1
fi
