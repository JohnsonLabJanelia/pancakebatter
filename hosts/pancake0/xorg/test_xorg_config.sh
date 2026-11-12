#!/usr/bin/env bash
# Try an xorg.conf on a throwaway X server, without touching gdm or the desktop session.
#
#   sudo ./test_xorg_config.sh CONFIG [SECONDS]      (default 20 s)
#
# Starts `Xorg :9` on VT 8 with CONFIG, which takes over the monitors while it runs. After 5 s it
# records `xrandr` for screens :9.0 and :9.1. When SECONDS are up (or on Ctrl+C or any error) it stops
# that server and switches back to the desktop's VT, so the desktop comes back on its own. The
# installed /etc/X11/xorg.conf and gdm are never touched.
#
# Results go to /var/log/xorg-test/<time>/ (Xorg.log, xrandr-screen0.txt, xrandr-screen1.txt,
# summary.txt), readable by everyone.
set -euo pipefail

CONFIG="${1:?usage: sudo $0 CONFIG [SECONDS]}"
SECONDS_UP="${2:-20}"
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }
[[ -f "$CONFIG" ]] || { echo "no such file: $CONFIG" >&2; exit 1; }
[[ "$SECONDS_UP" =~ ^[0-9]+$ && "$SECONDS_UP" -ge 8 && "$SECONDS_UP" -le 600 ]] || { echo "SECONDS: 8-600" >&2; exit 1; }
[[ -e /tmp/.X11-unix/X9 ]] && { echo "display :9 is already in use" >&2; exit 1; }
CONFIG="$(readlink -f "$CONFIG")"

USER_NAME="${SUDO_USER:?run through sudo}"
# The VT of the user's active graphical session: where to switch back to.
BACK_VT="$(loginctl list-sessions --no-legend | while read -r id _ user _; do
    [[ "$user" == "$USER_NAME" ]] || continue
    [[ "$(loginctl show-session "$id" -p Type --value)" == x11 ]] || continue
    vt="$(loginctl show-session "$id" -p VTNr --value)"; [[ "$vt" -gt 0 ]] && echo "$vt"
done | head -1)"
[[ -n "$BACK_VT" ]] || { echo "could not find the desktop VT" >&2; exit 1; }

OUT="/var/log/xorg-test/$(date +%Y%m%dT%H%M%S)"
mkdir -p "$OUT"
AUTH="$OUT/auth"
xauth -q -f "$AUTH" add :9 . "$(mcookie)"
chown "$USER_NAME" "$AUTH"
sha256sum "$CONFIG" > "$OUT/config.sha256"

XPID=""
finish() {
    [[ -n "$XPID" ]] && kill "$XPID" 2>/dev/null && wait "$XPID" 2>/dev/null || true
    chvt "$BACK_VT" || true
    rm -f "$AUTH"
    chmod -R a+rX "$OUT"
}
trap finish EXIT INT TERM

echo "starting Xorg :9 on VT 8 with $CONFIG for ${SECONDS_UP}s; the screens will go dark, then return to VT $BACK_VT"
Xorg :9 vt8 -config "$CONFIG" -logfile "$OUT/Xorg.log" -auth "$AUTH" -nolisten tcp -noreset &
XPID=$!
sleep 5
for s in 0 1; do
    sudo -u "$USER_NAME" env DISPLAY=":9.$s" XAUTHORITY="$AUTH" xrandr --query > "$OUT/xrandr-screen$s.txt" 2>&1 || true
done
sleep $((SECONDS_UP - 5))
finish
trap - EXIT INT TERM

{
    echo "config: $(cat "$OUT/config.sha256")"
    grep -E "ConnectedMonitor|CustomEDID|\(DFP-[0-9]\): connected|DFP-[0-9]: disconnected|No valid modes|Validated MetaModes|Virtual screen size|Setting mode|\(EE\)" "$OUT/Xorg.log" || true
    for s in 0 1; do echo "--- :9.$s"; grep -E "^Screen| connected|\*" "$OUT/xrandr-screen$s.txt" || true; done
} > "$OUT/summary.txt"
cat "$OUT/summary.txt"
echo "results: $OUT"
