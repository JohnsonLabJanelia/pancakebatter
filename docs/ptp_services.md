# PTP as systemd services

The cameras take their clock from the host over PTP: `ptp4l` runs as
grandmaster on the camera ports (`boundary_clock_jbod`, one clock per NIC
card), and `phc2sys -a -rr` pushes the system clock into each NIC's hardware
clock. Until 2026-10-05 both were started by hand after every boot:

```bash
sudo -b ptp4l -i mlnx1_p1_25g -i mlnx1_p2_25g -i mlnx2_p3_25g -i mlnx2_p4_25g -f /etc/ptp4l.conf -m
sudo -b phc2sys -a -rr -z /var/run/ptp4l -m
```

`install_ptp_units.sh` turns those two lines into `ptp4l.service` and
`phc2sys.service`, enabled at boot, with the same flags:

```bash
sudo ./install_ptp_units.sh                    # install + enable; starts now unless hand-started daemons exist
sudo ./install_ptp_units.sh --replace-running  # also kill the hand-started ones (refused while Orange records)
sudo ./install_ptp_units.sh --restart          # also restart running units so changes apply (refused while Orange runs)
sudo ./install_ptp_units.sh --uninstall
systemctl status ptp4l phc2sys --no-pager
journalctl -u ptp4l -u phc2sys -f
```

## What the units encode

- **Interfaces** come from `hosts/<hostname>/config.yml`: every NIC with
  `role: camera` and `expected_link: true`. Re-run the installer after moving a
  camera to another port (`camera_net_config.py move`).
- **Ordering**: after `network-online.target`, NetworkManager and the four
  `sys-subsystem-net-devices-<port>.device` units. ptp4l exits if a port does
  not exist, and `Restart=on-failure` with `RestartSec=15` and no start-rate
  limit keeps retrying until the NICs are back. A card whose firmware has
  thermally latched never comes back without a host power cycle
  ([`camera_nic_reset.md`](camera_nic_reset.md)); in that case the unit cycles
  every 15 s and `rig_health_check.py` reports the card missing.
- **phc2sys** `Requires=`, `After=` and `PartOf=ptp4l.service`: it starts after
  ptp4l, and restarting ptp4l restarts it too, which matters because its `-a`
  mode follows ptp4l over the `/var/run/ptp4l` socket.
- **CPUAffinity** is the non-isolated core list from `kernel_tuning`, the same
  one the health timer uses, so neither daemon lands on an acquisition core.
  (`isolcpus` already keeps them off; the unit just says so explicitly.)
- `-m` keeps the daemons logging to stdout, which under systemd means the
  journal. phc2sys prints one line per PHC per second; journald's default rate
  limit is far above that.
- `/etc/ptp4l.conf` is tracked as `configs/ptp4l.conf`. The installer copies
  it in when missing and only reports a difference when a different file is
  already installed.

## The UTC offset the grandmaster announces

PTP time is TAI-like: camera timestamps run 37 s ahead of UTC (TAI-UTC has been
37 s since 2017-01-01). The grandmaster says so in every Announce message as
`currentUtcOffset` plus a `currentUtcOffsetValid` flag. linuxptp 3.1.1 sends
the offset from `utc_offset` in `ptp4l.conf`, default 37, but announces it
**not valid**, and its config file has no option for the flag. Until
2026-10-11 pancake0 therefore announced `currentUtcOffset 37`,
`currentUtcOffsetValid 0`, `ptpTimescale 1`, `timeTraceable 0`,
`timeSource 0xa0` (internal oscillator). Orange's clock classifier rightly
refused to certify camera time as TAI on that basis, even though Palette had
checked camera_ts - 37 s = host CLOCK_REALTIME on every recording in its store.

`ptp4l.service` now runs `ptp4l_gm_settings` (`libexec/ptp4l_gm_settings.sh`,
installed root-owned to `/usr/local/libexec/pancakebatter/`) after each start.
It waits for ptp4l's management socket, reads `GRANDMASTER_SETTINGS_NP`, and
sends it back with only `currentUtcOffsetValid` set to 1. The setting lasts as
long as the ptp4l process, hence the hook. It refuses (and logs why) if ptp4l
announces a non-PTP timescale or an offset other than the one the leap-second
table gives (next section). Its `ExecStartPost=-`
prefix means a failure never stops ptp4l: the cameras keep their clock and only
the flag is missing.

Deliberately unchanged:

- **`timeTraceable` stays 0.** The chain is NTP (timesyncd) → system clock →
  `phc2sys -rr` → PHCs → cameras, so time is traceable to UTC only once
  timesyncd has synchronized, which is not true at boot. A flag set once at
  start would then be wrong for the first minute and wrong again whenever NTP
  is lost. Gating it on `NTPSynchronized` would need a periodic updater, and no
  consumer needs it today.
- **`timeSource` stays `0xa0`** (internal oscillator), `clockClass 248` and the
  other quality fields as ptp4l set them.

### Where 37 comes from

TAI-UTC is published by the IERS (International Earth Rotation and Reference
Systems Service), which announces each leap second about six months ahead:

- IERS Bulletin C, the announcement itself, issued every six months:
  <https://datacenter.iers.org/data/latestVersion/bulletinC.txt>. Bulletin C 72
  (checked 2026-10-12): "from 2017 January 1, 0h UTC, until further notice:
  UTC-TAI = -37 s" and "NO leap second will be introduced at the end of
  December 2026".
- The machine-readable table, `leap-seconds.list`, kept by IANA with the time
  zone database and mirrored by the IERS:
  <https://data.iana.org/time-zones/tzdb/leap-seconds.list>,
  <https://hpiers.obspm.fr/iers/bul/bulc/ntp/leap-seconds.list>. Last entry
  `3692217600 37 # 1 Jan 2017`; the copy checked on 2026-10-12 expires
  28 June 2027.
- Ubuntu's `tzdata` package installs that table as
  `/usr/share/zoneinfo/leap-seconds.list`, updated with ordinary package
  updates.
- IEEE 1588 defines `currentUtcOffset` as TAI-UTC; `man ptp4l` documents
  `utc_offset` ("The current offset between TAI and UTC. The default is 37").

`ptp4l_gm_settings` takes the expected offset from the installed table, not
from a constant. It declares the flag only if the table passes its own SHA-1
check (the `#h` line), has not expired (`#@` line), and its TAI-UTC for now
equals the `currentUtcOffset` ptp4l announces. Within 90 days of the table's
expiry it still declares the flag but logs a warning. After expiry, or on any
mismatch, it leaves the flag unset and logs why. Recordings then classify the
cameras `ieee1588_ptp_timescale_untraceable`, not TAI, until it is fixed.

When the IERS announces a leap second: install the updated `tzdata` before the
leap second takes effect, set `utc_offset` in `configs/ptp4l.conf` and
`/etc/ptp4l.conf` to the new value on the leap-second date, and restart ptp4l
with no Orange running (`sudo ./install_ptp_units.sh --restart`). Between those
two steps the table and ptp4l disagree, so the flag is deliberately not
declared.

Check it (root, because the socket is `root:root 0660`):

```bash
sudo pmc -u -b 0 -s /var/run/ptp4l 'GET TIME_PROPERTIES_DATA_SET'   # currentUtcOffsetValid 1
journalctl -u ptp4l -b | grep ptp4l_gm_settings                     # "currentUtcOffset 37 declared valid (matches ...)"
```

## Clock direction and NTP

`phc2sys -rr` makes the system clock the source for the PHCs because the host
is the grandmaster: whatever happens to the system clock, the camera clocks
follow.

As of 2026-10-05 nothing disciplines the system clock on pancake0:
`timedatectl` shows `NTP=no`, no timesyncd or chrony is active, and the DHCP
lease carries no NTP server. Measured against pool.ntp.org and
time.google.com the clock was **478.7 s (8 min) behind**, and the hardware RTC
is only written back by the kernel while NTP is synchronized, so every boot
starts from an already-wrong time. The camera timeline is self-consistent but
cannot be lined up with anything outside the rig. `rig_health_check.py`'s
`time_sync` check reports this.

The fix is one command:

```bash
sudo timedatectl set-ntp true      # enables systemd-timesyncd; UDP 123 outbound works, Ubuntu's default servers answer
timedatectl                        # "System clock synchronized: yes" within a minute
```

**Do it between recordings, not during one.** The first synchronization is a
*step*: the system clock jumps forward by the whole offset (8 min today) and,
through `phc2sys -rr`, so do the camera PHCs, so a recording in progress would
contain an 8 minute discontinuity in its timestamps.

Why one command is enough for a clock that drifts continuously: timesyncd is
not a one-shot "set the time at boot". It stays running, polls its server
every 32 s to 34 min (backing off while things look stable), and corrects the
clock in two ways. A large offset is fixed by a step; the normal small ones are
fixed by *slewing*, telling the kernel to run the clock slightly fast or slow
until the error is gone. While doing that it learns the crystal's rate error
and feeds it to the kernel as a standing frequency correction (`adjtimex`), so
after a few polls the clock is no longer drifting at all between polls; it is
being told in advance to tick about 350 ppm faster. The slew is capped at
500 ppm, which the PTP chain follows smoothly, and the kernel rewrites the RTC
every 11 minutes while synchronized so later boots start right. The
`time_sync` check reports the remaining offset; with NTP on it should sit in
the millisecond range.

Background on the crystal, ppm, and which clock code should read is in
[`time_and_clocks.md`](time_and_clocks.md).

`tsc=reliable` stays. It tells the kernel not to let the clocksource watchdog
demote the TSC (which happened on 2026-09-11 and cost a week of inflated
host-side timings, see `kernel_tuning.md`); it says nothing about the clock's
*rate*. NTP corrects the rate through `adjtimex`, exactly as on any other host.
The drift seen here is roughly 350 ppm, inside the 500 ppm timesyncd can hold;
if the `time_sync` check ever shows the offset creeping up with NTP enabled,
the TSC calibration is worse than that and chrony (which can step
periodically) would be the next step.

## Interaction with the NIC reset tool

`reset_camera_nics.py` recognises daemons that run under these units (by their
cgroup) and, with `--restart-ptp`, restarts them through `systemctl restart
ptp4l.service` instead of kill-and-respawn. The hand-started case still works
the old way. Since the ports vanish when a card halts, a ptp4l that was already
running keeps logging `ioctl SIOCGIFINDEX failed: No such device` rather than
exiting, so the restart after a recovered card remains necessary either way.
