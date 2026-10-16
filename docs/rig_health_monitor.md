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
| `ptp` | `pgrep ptp4l`/`phc2sys`, `journalctl _COMM=phc2sys` | process missing: warn; any port whose latest phc2sys sample is not servo state `s2` (locked): crit; worst *locked* `sys offset` in 5 min >100 µs warn, >10 ms crit (the step at start-up is `s0`/`s1` and is not counted once locked); `--no-ptp` skips. With the units from `install_ptp_units.sh` the daemons come back at boot, so this only fires when they are really down |
| `time_sync` | `timedatectl show`, one SNTP packet to `--ntp-server` (default pool.ntp.org) | no NTP service or not synchronized: warn; clock off by >1 s warn, >300 s crit; `--no-ntp-query` skips the packet |
| `pcie_aer` | `journalctl -k --since <last run>` | any `AER:` line: warn, with the device |
| `storage_errors` | `journalctl -k --since <last run>` | any `critical medium error`, nvme `I/O Error`, `I/O error, dev`, `Buffer I/O error`, nvme timeout or controller reset: crit per drive, and it **stays crit** on later runs until the drive is acknowledged |
| `nvme_smart` | `/var/lib/pancakebatter-nvme/smart.json` (root snapshot, below) | `critical_warning` ≠ 0, media errors above the acknowledged baseline (any, if never acknowledged), available spare at or below its threshold: crit. Increases since the last run in error-log entries, unsafe shutdowns, warning/critical temperature minutes, and `percent_used` ≥ 80: warn. Snapshot older than 15 min: warn. Not installed: info |
| `nvme_firmware` | `/sys/class/nvme/*/{model,firmware_rev,serial}` | same-model drives on different firmware, or firmware differing from `storage_devices` in the host config: warn |
| `acquisition` | `pgrep` for `targets/release/orange` (argv[0] checked) | info only |

Thresholds are overridable per run: `--threshold nic_warn=80 --threshold disk_warn_pct=15`.

On a terminal the findings are coloured by level (green ok, cyan info, yellow
warn, bold red crit). The script uses `rich` when the interpreter has it and
plain ANSI codes when it does not, so colour never depends on an install;
`rich` adds aligned, wrapped columns. It is in `packages/apt/python.txt` as
`python3-rich` for the system python, or `pip install rich` in a conda env.
`--color always|never` overrides the terminal detection and `NO_COLOR` is
honoured. Under the timer (journal), in a pipe and in alert mails the output
is always plain text.
Only non-ok findings are listed individually; ok readings collapse to one line
per check with the highest value.

## Drive health

Added after 2026-10-16, when `/mnt/Data1` (nvme1n1, Sabrent Rocket 4 Plus on
R4PTLC.3) failed reads during Orange's finalize of a 95-minute run. SMART had
already counted 1,495 media errors and the kernel logged 217 `critical medium
error` lines, while the 00:00 and 00:05 runs reported ok.

**SMART needs root.** `/dev/nvmeN` is mode 0600 and the health unit runs
unprivileged with `NoNewPrivileges`, so a separate root unit takes the reading:

```bash
sudo ./install_nvme_smart_snapshot.sh              # every 5min; --interval 2min to change
sudo ./install_nvme_smart_snapshot.sh --uninstall
```

It installs a root-owned copy of `libexec/nvme_smart_snapshot.py` to
`/usr/local/libexec/pancakebatter/nvme_smart_snapshot` (re-run the installer after
editing it), and `nvme-smart-snapshot.timer`, which runs
`nvme smart-log /dev/nvmeN -o json` for every controller and writes
`/var/lib/pancakebatter-nvme/smart.json` (0644). Nothing else is written, no
device is changed, and the unit has no network and the same idle scheduling and
CPU pinning as the health check.

**Acknowledging a drive.** Media errors and kernel storage errors are sticky:
a drive stays crit until someone records that its errors are known. That sets the
baseline to the drive's current `media_errors`, so only new ones alert again:

```bash
./rig_health_check.py --state-dir /var/lib/rig-health --acknowledge-drive <serial> --note "rescued; RMA"
```

The drive can be named by serial, `nvmeN`, or a namespace/partition (`nvme1n1p1`).
Acknowledgements live in `/var/lib/rig-health/drive_acks.json`.

**Pre-recording gate.** Before a record start, the recorder can ask whether the
drive under its recording root is fit to record onto:

```bash
./rig_health_check.py --preflight --recording-root /mnt/Data2/orange --expected-gb 333 --state-dir /var/lib/rig-health
```

It finds the drive through the mount (`st_dev` → `/sys/dev/block`) and judges
only that drive: unacknowledged media errors, a SMART critical warning, spare at
threshold, or kernel storage errors are exit 2; so is free space below
`--expected-gb` plus 10 % (`--threshold preflight_margin_pct=N`). Softer signals
are exit 1, healthy is 0. It reads the timer's state for its journal window and
acknowledgements but writes nothing and sends no mail. Calling it, and refusing to
start on exit 2, is up to the recorder (Orange owns the record start); a 95-minute
run is about 333 GB.

## Not interfering with acquisition

- The script renices itself to 19 and pins itself to the complement of
  `kernel_tuning.isolated_cores` from `hosts/<hostname>/config.yml`
  (`./rig_health_check.py --print-affinity` shows the list; on pancake0 that
  is everything except CPUs 1,2,6,8,10,12,33,34,38,40,42,44, where Orange's
  YOLO threads and Citrus's render and arena update threads run, each with an
  idle hyperthread sibling).
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
