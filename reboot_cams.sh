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
#   RIG_CONTROL_PYTHON     python with pexpect/pyyaml/rich for pdu.py (default: the
#                          rig_control conda env if found, else python3 on PATH)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Coloured messages on a terminal only; pipes and log files stay plain. NO_COLOR is honoured and
# REBOOT_CAMS_COLOR=always|never overrides the detection.
_color() {  # _color <fd>: should output on that file descriptor be coloured?
  case "${REBOOT_CAMS_COLOR:-auto}" in always) return 0 ;; never) return 1 ;; esac
  [[ -t "$1" && -z "${NO_COLOR:-}" ]]
}
step() {  # progress
  if _color 1; then printf '\033[1;36m[reboot_cams]\033[0m %s\n' "$*"; else printf '[reboot_cams] %s\n' "$*"; fi
}
good() {  # success
  if _color 1; then printf '\033[1;32m[reboot_cams]\033[0m \033[32m%s\033[0m\n' "$*"; else printf '[reboot_cams] %s\n' "$*"; fi
}
note() {  # problem detail, to stderr
  if _color 2; then printf '\033[1;31m[reboot_cams]\033[0m \033[31m%s\033[0m\n' "$*" >&2; else printf '[reboot_cams] %s\n' "$*" >&2; fi
}
die() {   # die <exit code> <message>
  local code="$1"; shift
  note "$*"
  exit "$code"
}
source "$HERE/lib/host_config.sh"
PDU_HOST="${PDU_HOST:-192.168.20.177}"
# Python for pdu.py (needs pexpect, pyyaml, rich): $RIG_CONTROL_PYTHON if set, else the rig_control
# conda env wherever conda lives, else python3 on PATH. Checked before anything is power-cycled.
if [[ -z "${RIG_CONTROL_PYTHON:-}" ]]; then
  for base in "${CONDA_EXE:+$(dirname "$(dirname "$CONDA_EXE")")}" "$HOME/miniforge3" "$HOME/mambaforge" "$HOME/miniconda3" /opt/conda; do
    if [[ -n "$base" && -x "$base/envs/rig_control/bin/python" ]]; then
      RIG_CONTROL_PYTHON="$base/envs/rig_control/bin/python"; break
    fi
  done
  RIG_CONTROL_PYTHON="${RIG_CONTROL_PYTHON:-python3}"
fi
if ! "$RIG_CONTROL_PYTHON" -c 'import pexpect, yaml, rich' 2>/dev/null; then
  note "$RIG_CONTROL_PYTHON cannot import pexpect/yaml/rich (needed by pdu.py). Create the env with"
  note "  conda env create -f environments/rig_control.yaml"
  die 2 "or point RIG_CONTROL_PYTHON at a python that has them. Nothing was power-cycled."
fi
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
    *) die 2 "unknown argument: $1 (see --help)" ;;
  esac
  shift
done
[[ -x "$READY_BIN" ]] || die 2 "missing $READY_BIN (build target evt_camera_ready)"

# Serials come from the PDU outlet descriptions in hosts/<hostname>/config.yml ("SN: 2010093").
SERIALS="$("$RIG_CONTROL_PYTHON" - "$HOST_CONFIG_FILE" "$PDU_HOST" <<'PY'
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
[[ -n "$SERIALS" ]] || die 2 "no camera serials found in hosts/<hostname>/config.yml for PDU $PDU_HOST"

# -x matches the process name exactly; -f on the path also matched any shell that merely mentioned it
if pgrep -x orange >/dev/null; then
  die 3 "Orange is running; stop it before power-cycling the cameras."
fi

STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_DIR="$HERE/logs/camera_power_cycle"; mkdir -p "$LOG_DIR"
REPORT="$LOG_DIR/camera_ready_${STAMP}.json"

step "PDU $PDU_HOST outlet=$OUTLET reboot ($STAMP)"
(cd "$HERE" && "$RIG_CONTROL_PYTHON" ./pdu.py --host "$PDU_HOST" --action reboot --outlet "$OUTLET" --verify)

step "waiting for cameras $SERIALS (up to ${WAIT_SECONDS}s), then checking lens mounts"
ARGS=(--serials "$SERIALS" --wait-seconds "$WAIT_SECONDS" --config-dir "$ORANGE_CAMERA_CONFIG" --json "$REPORT")
(( APPLY )) && ARGS+=(--apply-lens)
if "$READY_BIN" "${ARGS[@]}"; then
  good "cameras ready; report $REPORT"
else
  die 1 "CAMERA/LENS CHECK FAILED; report $REPORT"
fi
