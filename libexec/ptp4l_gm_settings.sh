#!/usr/bin/env bash
# Run by ptp4l.service (ExecStartPost) as root: declare the grandmaster's UTC offset valid, so
# the Announce messages and TIME_PROPERTIES_DATA_SET state TAI-UTC explicitly
# (currentUtcOffsetValid 1) instead of leaving consumers to assume it.
#
# linuxptp 3.1.1 has no config option for the flag: ptp4l.conf's utc_offset (default 37) sets the
# value but ptp4l announces it as not valid. GRANDMASTER_SETTINGS_NP over the management socket is
# the supported way, and it lasts only as long as the ptp4l process, hence the unit hook.
#
# Every other field (clockClass, clockAccuracy, leap flags, timeTraceable, timeSource, ...) is
# read back from ptp4l and resent unchanged. Installed root-owned by install_ptp_units.sh to
# /usr/local/libexec/pancakebatter/ptp4l_gm_settings; see docs/ptp_services.md.
set -euo pipefail

# TAI-UTC in seconds. 37 since 2017-01-01; IERS Bulletin C announces any change ~6 months ahead.
EXPECTED_UTC_OFFSET=37
SOCKET=/var/run/ptp4l
PMC=("${PTP4L_GM_PMC:-/usr/sbin/pmc}" -u -b 0 -s "$SOCKET")   # override only for tests

gm_settings() {
    "${PMC[@]}" "GET GRANDMASTER_SETTINGS_NP" 2>/dev/null | awk '
        /GRANDMASTER_SETTINGS_NP/ {in_block = 1; next}
        in_block && NF == 2 {print $1, $2}'
}

# ptp4l is Type=simple: wait for its management socket to answer (up to ~30 s).
current=""
for _ in $(seq 60); do
    current="$(gm_settings || true)"
    [[ -n "$current" ]] && break
    sleep 0.5
done
if [[ -z "$current" ]]; then
    echo "ptp4l_gm_settings: no GRANDMASTER_SETTINGS_NP reply from $SOCKET; flag not set" >&2
    exit 1
fi

declare -A gm=()
while read -r key value; do gm["$key"]="$value"; done <<< "$current"

fields=(clockClass clockAccuracy offsetScaledLogVariance currentUtcOffset leap61 leap59
        currentUtcOffsetValid ptpTimescale timeTraceable frequencyTraceable timeSource)
for f in "${fields[@]}"; do
    [[ -n "${gm[$f]:-}" ]] || { echo "ptp4l_gm_settings: field $f missing from reply" >&2; exit 1; }
done
if [[ "${gm[currentUtcOffset]}" != "$EXPECTED_UTC_OFFSET" ]]; then
    echo "ptp4l_gm_settings: ptp4l announces currentUtcOffset ${gm[currentUtcOffset]}, expected" \
         "$EXPECTED_UTC_OFFSET; not declaring it valid (check utc_offset in /etc/ptp4l.conf)" >&2
    exit 1
fi
if [[ "${gm[ptpTimescale]}" != 1 ]]; then
    echo "ptp4l_gm_settings: ptpTimescale is ${gm[ptpTimescale]}, not 1 (PTP/TAI); not changing it" >&2
    exit 1
fi

gm[currentUtcOffsetValid]=1
set_args=""
for f in "${fields[@]}"; do set_args+="$f ${gm[$f]} "; done
"${PMC[@]}" "SET GRANDMASTER_SETTINGS_NP ${set_args% }" >/dev/null

after="$(gm_settings)"
if grep -qx "currentUtcOffsetValid 1" <<< "$after"; then
    echo "ptp4l_gm_settings: currentUtcOffset ${gm[currentUtcOffset]} declared valid" \
         "(clockClass ${gm[clockClass]}, timeTraceable ${gm[timeTraceable]}, timeSource ${gm[timeSource]})"
else
    echo "ptp4l_gm_settings: SET sent but currentUtcOffsetValid did not read back as 1" >&2
    exit 1
fi
