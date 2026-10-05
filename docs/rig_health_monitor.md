# Rig health monitor

`rig_health_check.py` is a read-only check of the things that have actually
taken the rig down, run every five minutes by a systemd timer that is kept off
the acquisition cores. It alerts on changes; it never fixes anything.

```bash
./rig_health_check.py                 # findings; exit 0 ok, 1 warnings, 2 critical
./rig_health_check.py --json
sudo ./install_rig_health_timer.sh    # install the timer for $SUDO_USER (every 5min)
sudo ./install_rig_health_timer.sh --interval 2min --user rig
sudo ./install_rig_health_timer.sh --uninstall
```

## Checks

| check | source | levels |
|---|---|---|
| `camera_nics` | `/sys/bus/pci/devices/<pcie_id>/net`, `carrier` | netdev missing: crit (names the card and `reset_camera_nics.py`); `expected_link` port without carrier: warn |
| `mlx5_health` | `journalctl -k --since <last run>` | any `temp_warn` / `Health issue observed` / `health recovery flow aborted` line: crit |
| `nic_temps` | hwmon `mlx5` (ASIC and transceiver modules) | ASIC warn 85 / crit 95 C, module warn 65 / crit 73 C; a sensor reading 0 C is what a halted firmware returns: warn, one line per card |
| `cpu_temp` | hwmon `k10temp` Tctl | warn 85 / crit 95 |
| `nvme_temps` | hwmon `nvme` Composite | the drive's own `max` (warn) and `crit` |
| `gpu_temps` | `nvidia-smi --query-gpu=temperature.gpu` | warn 85 / crit 92; `--no-gpu` skips |
| `disk_space` | `statvfs` on `/` and the `/mnt` partitions in the host config | warn <10 % free, crit <3 % |
| `ptp` | `pgrep ptp4l`/`phc2sys`, `journalctl _COMM=phc2sys` | process missing: warn; worst `sys offset` in 5 min >100 µs warn, >10 ms crit; `--no-ptp` skips. With the units from `install_ptp_units.sh` the daemons come back at boot, so this only fires when they are really down |
| `time_sync` | `timedatectl show`, one SNTP packet to `--ntp-server` (default pool.ntp.org) | no NTP service or not synchronized: warn; clock off by >1 s warn, >300 s crit; `--no-ntp-query` skips the packet |
| `pcie_aer` | `journalctl -k --since <last run>` | any `AER:` line: warn, with the device |
| `acquisition` | `pgrep` for `targets/release/orange` (argv[0] checked) | info only |

Thresholds are overridable per run: `--threshold nic_warn=80 --threshold disk_warn_pct=15`.
Only non-ok findings are listed individually; ok readings collapse to one line
per check with the highest value.

## Not interfering with acquisition

- The script renices itself to 19 and pins itself to the complement of
  `kernel_tuning.isolated_cores` from `hosts/<hostname>/config.yml`
  (`./rig_health_check.py --print-affinity` shows the list; on pancake0 that
  is everything except cores 1,2,6,8,10,12,38,40,42,44 where Orange's
  acquisition and YOLO threads live).
- The unit adds `Nice=19`, `IOSchedulingClass=idle`, `CPUSchedulingPolicy=idle`
  and `CPUAffinity=` with the same list, plus `ProtectSystem=strict`: it can
  write only to `/var/lib/rig-health`.
- Everything is a read of sysfs, `/proc` or the journal. The NIC temperatures
  come from the hwmon entries the driver already maintains; nothing talks to
  the firmware through MFT. The one external process is `nvidia-smi`
  (`--no-gpu` in `RIG_HEALTH_ARGS` turns it off if a soak ever shows it mattering).
- A run takes well under a second plus `nvidia-smi`; `TimeoutStartSec=120`
  bounds it.

## Alerts and history

Each run prints its findings to the journal (`journalctl -u rig-health.service`).
With `--state-dir` (the unit passes `/var/lib/rig-health`) it keeps
`last.json`, appends one line per run to `history.jsonl` (trimmed at ~8 MB),
and compares each check's level with the previous run. A level change
(`ok -> warn`, `crit -> ok`, ...) is a transition; when `RIG_HEALTH_EMAIL` is set
in `/etc/default/rig-health` a short mail with the full report goes out and no
repeat mails follow while a state persists.

Mail goes through `msmtp -C /etc/msmtprc -t` (override with
`RIG_HEALTH_MSMTP_CONFIG`; `sendmail -t` is the fallback). On pancake0 that
system config is a Gmail account, `pancake0.status@gmail.com`, with its app
password in `/etc/msmtp/gmail-app-password`, readable by the `msmtp-users`
group, so the service user has to be in that group (`jeremy` and the domain
user `delahantyj` are). It has delivered to `delahantyj@janelia.hhmi.org`
before. The system config is used on purpose: a per-user `~/.msmtprc` can be
missing or point at a password file that is not there (jeremy's does, as of
2026-10-05), and the unit's `ProtectHome=read-only` would block its log file.
Send yourself one message to confirm the path end to end:

```bash
./rig_health_check.py --test-mail delahantyj@janelia.hhmi.org
```

```bash
systemctl list-timers rig-health.timer
journalctl -u rig-health.service -n 40
cat /var/lib/rig-health/last.json
sudo systemctl restart rig-health.service     # run now (after editing /etc/default/rig-health)
```

## cron instead of a timer

If you prefer a crontab, the equivalent line (as the rig user, with the same
priority and affinity handling; the script pins itself regardless) is:

```
*/5 * * * * cd /home/jeremy/pancakebatter && nice -n 19 ionice -c3 /usr/bin/python3 ./rig_health_check.py --state-dir "$HOME/.local/state/rig-health" >> "$HOME/.local/state/rig-health/cron.log" 2>&1
```

The timer is preferred because journald keeps the output, `systemctl status`
shows the last result, and the idle scheduling classes are declared in one place.
