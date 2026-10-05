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
