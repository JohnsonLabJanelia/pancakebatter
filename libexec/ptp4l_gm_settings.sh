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

# TAI-UTC comes from the IERS leap-second table that tzdata installs (the IANA/IERS
# leap-seconds.list; see docs/ptp_services.md for the published sources). The flag is declared
# only if the table's own SHA-1 checks out, it has not expired, and its current TAI-UTC equals
# what ptp4l announces (utc_offset in /etc/ptp4l.conf). Any failure leaves the flag unset.
SOCKET=/var/run/ptp4l
PMC=("${PTP4L_GM_PMC:-/usr/sbin/pmc}" -u -b 0 -s "$SOCKET")   # override only for tests
LEAP_SECONDS_LIST="${PTP4L_GM_LEAP_SECONDS_LIST:-/usr/share/zoneinfo/leap-seconds.list}"
NOW_UNIX="${PTP4L_GM_NOW_UNIX:-$(date +%s)}"                    # override only for tests
NTP_MINUS_UNIX=2208988800   # leap-seconds.list counts seconds from 1900-01-01

# Prints "<TAI-UTC> <expiry unix> <expiry date>" for NOW_UNIX, or fails with a reason.
leap_table_offset() {
    local list="$1" want got
    [[ -r "$list" ]] || { echo "cannot read $list (tzdata installed?)" >&2; return 1; }
    # The file's "#h" line is the SHA-1 of the "#$" and "#@" values and every data line's two
    # numbers, concatenated without whitespace (format described in the file's own header).
    want="$(awk '/^#h/ {for (i = 2; i <= NF; i++) printf "%08s", $i}' "$list" | tr ' ' 0)"
    got="$(awk '/^#\$/ {s = s $2} /^#@/ {s = s $2} /^[0-9]/ {s = s $1 $2} END {printf "%s", s}' \
        "$list" | sha1sum | cut -d' ' -f1)"
    [[ -n "$want" && "$want" == "$got" ]] || { echo "$list fails its own SHA-1 check" >&2; return 1; }
    local expires_ntp expires_unix
    expires_ntp="$(awk '/^#@/ {print $2}' "$list")"
    [[ "$expires_ntp" =~ ^[0-9]+$ ]] || { echo "$list has no expiry line" >&2; return 1; }
    expires_unix=$((expires_ntp - NTP_MINUS_UNIX))
    if (( NOW_UNIX >= expires_unix )); then
        echo "$list expired on $(date -u -d "@$expires_unix" +%F); update tzdata" >&2
        return 1
    fi
    local offset
    offset="$(awk -v now=$((NOW_UNIX + NTP_MINUS_UNIX)) '
        /^[0-9]/ && $1 <= now {dtai = $2} END {print dtai}' "$list")"
    [[ "$offset" =~ ^[0-9]+$ ]] || { echo "$list has no entry before now" >&2; return 1; }
    echo "$offset $expires_unix $(date -u -d "@$expires_unix" +%F)"
}

if ! leap="$(leap_table_offset "$LEAP_SECONDS_LIST")"; then
    echo "ptp4l_gm_settings: no verified TAI-UTC from the leap-second table; flag not set" >&2
    exit 1
fi
read -r EXPECTED_UTC_OFFSET LEAP_EXPIRES_UNIX LEAP_EXPIRES_DATE <<< "$leap"
if (( LEAP_EXPIRES_UNIX - NOW_UNIX < 90 * 86400 )); then
    echo "ptp4l_gm_settings: warning: $LEAP_SECONDS_LIST expires $LEAP_EXPIRES_DATE;" \
         "update tzdata or the flag will stop being declared" >&2
fi

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
         "$EXPECTED_UTC_OFFSET from $LEAP_SECONDS_LIST; not declaring it valid" \
         "(check utc_offset in /etc/ptp4l.conf)" >&2
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
         "(matches $LEAP_SECONDS_LIST, expires $LEAP_EXPIRES_DATE;" \
         "clockClass ${gm[clockClass]}, timeTraceable ${gm[timeTraceable]}, timeSource ${gm[timeSource]})"
else
    echo "ptp4l_gm_settings: SET sent but currentUtcOffsetValid did not read back as 1" >&2
    exit 1
fi
