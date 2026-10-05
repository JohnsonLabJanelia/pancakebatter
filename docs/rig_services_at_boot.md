# What runs at boot on the rig host

Which rig services come back on their own after a power cycle, which were set
up once, and which have to be re-run when the configuration changes.

## Starts automatically on every boot

| What | Mechanism | Notes |
|---|---|---|
| Time synchronization | `systemd-timesyncd`, enabled once by `sudo timedatectl set-ntp true` | Polls NTP servers continuously, not just at boot (see [`ptp_services.md`](ptp_services.md)). Writes the RTC every 11 min while synchronized, so even the first seconds of a boot are close. |
| PTP grandmaster for the cameras | `ptp4l.service`, installed by `install_ptp_units.sh` | Starts after the camera NIC device units. If a NIC is missing it fails and retries every 15 s indefinitely. |
| NIC hardware clocks follow the system clock | `phc2sys.service`, `PartOf=ptp4l.service` | Starts after ptp4l; restarts whenever ptp4l does. |
| Health monitor | `rig-health.timer`, installed by `install_rig_health_timer.sh` | First run 3 min after boot, then every 5 min. Reads `/etc/default/rig-health` on each run (mail recipient). State in `/var/lib/rig-health` survives reboots, so the first run after a boot reports transitions against the last run before shutdown. |
| Camera NIC names and addresses | udev rule `/etc/udev/rules.d/70-network-aliases.rules` + NetworkManager profiles (`autoconnect=yes`) | Written by `network_alias_assignment.sh` / `create_nm_connections.sh` / `configure_interfaces.sh`. |
| Kernel tuning | GRUB command line, `/etc/sysctl.d/90-orange-writeback.conf`, THP settings | `check_kernel_tuning.py` verifies the live host against `hosts/<host>/config.yml`. |

## Done once; nothing to repeat

- The host power-off that clears a thermally latched ConnectX firmware
  ([`camera_nic_reset.md`](camera_nic_reset.md)). A boot cannot do this; if a
  card latches again, the health check reports it missing within three minutes
  of startup and `ptp4l.service` keeps retrying until the next power cycle.
- `sudo systemctl restart rig-health.service` after editing
  `/etc/default/rig-health`: forces one run so the mail path is exercised. The
  timer takes over from there.
- `./rig_health_check.py` and `./reset_camera_nics.py` by hand are spot checks
  of the same things the timer and the installers set up.

## Re-run when the configuration changes

| After changing | Re-run | Why |
|---|---|---|
| Which ports carry cameras (`camera_net_config.py move`, `expected_link`) | `sudo ./install_ptp_units.sh` | The interface list is baked into `ptp4l.service`. |
| `kernel_tuning.isolated_cores` | `sudo ./install_ptp_units.sh` and `sudo ./install_rig_health_timer.sh` | Both units embed the non-isolated CPU list. |
| Location of the checkout | `sudo ./install_rig_health_timer.sh` | The unit's `ExecStart` and `WorkingDirectory` point at it. |
| `configs/ptp4l.conf` | `sudo ./install_ptp_units.sh` (it reports a difference; copy the file in by hand if you want the new one) | `/etc/ptp4l.conf` is never overwritten. |
| Camera serials / PDU outlets | nothing to install | `reboot_cams.sh` reads the host config at run time. |

## Not managed by this repo (still hand-started)

Orange itself, and any recording session. The health check reports whether it
is running (`acquisition`) and the reset tools refuse to act while it is.
