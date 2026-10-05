# Camera NIC status and in-place firmware reset

`reset_camera_nics.py` reports the state of every ConnectX card listed in
`hosts/<hostname>/config.yml` (one card = the four PCI functions sharing a
`pcie_id` bus, e.g. `61:00.0-3`) and, with `--reset`, brings a halted card back
without rebooting the host.

```bash
./reset_camera_nics.py                      # status; exit 1 if any card is missing netdevs
./reset_camera_nics.py --json               # the same for scripts (rig_health_check.py imports the module)
sudo ./reset_camera_nics.py --reset --dry-run     # print the plan, change nothing
sudo ./reset_camera_nics.py --reset               # reset every unhealthy card
sudo ./reset_camera_nics.py --reset --card 61:00 --restart-ptp
```

## What happened on 2026-10-03 (why this exists)

| Time (EDT) | Kernel log |
|---|---|
| 05:56:38 | `mlx5_core 0000:49:00.x: temp_warn: High temperature on sensors ...` (all four functions) |
| 05:57:00 | same on `0000:61:00.x` |
| 05:58:06 | `49:00.x: Health issue observed, High temperature, severity(2) CRITICAL`, `synd 0x10` |
| 05:59:24 | `49:00.x` netdevs removed; NetworkManager: `mlnx2_p* removed` |
| 06:00:24 | `61:00.x` CRITICAL, same syndrome |
| 06:00:29 | `mlnx1_p*` removed |
| 06:01:36 | `health recovery flow aborted, PCI reads still not working` on all eight |

Two separate cards hit the firmware thermal cutoff within 30 s of each other,
with no earlier mlx5 messages in the preceding 30 hours and the CPU, NVMe and
GPUs cool afterwards: an airflow or room-cooling event, not a bad card. The
cards stayed bound to `mlx5_core` with their PCI config space readable, but
without netdevs, hwmon sensors reading 0 C, and `ptp4l`/`phc2sys` still running
against the vanished interfaces. Nothing noticed for two days, which is what
[`rig_health_monitor.md`](rig_health_monitor.md) is for.

## What `--reset` does

1. Refuses if Orange is running (`targets/release/orange`) unless `--force`,
   and only touches cards that are missing netdevs unless you name them with
   `--card`.
2. Writes a transcript to `logs/nic_reset/nic_reset_<stamp>.log`, starting with
   the mlx5 health lines from this boot for each function (the post-mortem
   evidence, before it is cleared by the reset).
3. `mst start`, then `mlxfwreset -d <card>.0 query`. If reset level 3 ("Driver
   restart and PCI reset") is supported: `mlxfwreset -d <card>.0 --level 3 reset -y`.
4. If the query fails or level 3 is unsupported (`--method auto`, the default),
   falls back to the PCI route that level 3 performs internally: remove the four
   functions through sysfs, pulse Secondary Bus Reset (bit 6 of `BRIDGE_CONTROL`)
   on the upstream bridge with `setpci`, rescan the bridge. `--method mlxfwreset`
   or `--method pci` picks one explicitly.
5. Waits up to `--wait` seconds (default 90) for the netdevs to reappear under
   their configured names (the udev rules in
   `/etc/udev/rules.d/70-network-aliases.rules` rename by MAC).
6. NetworkManager re-activates the static profiles on its own
   (`autoconnect=yes`); any `managed: true` port still not connected gets
   `nmcli connection up <name>`.
7. PTP: `ptp4l`/`phc2sys` keep running but their sockets were bound to the old
   devices. If they run under the systemd units from `install_ptp_units.sh`
   ([`ptp_services.md`](ptp_services.md)) the tool prints or runs
   `systemctl restart ptp4l.service`; for hand-started daemons it prints the
   kill/relaunch recipe built from their command lines, or does it with
   `--restart-ptp` (logs under `logs/nic_reset/`).
8. Prints the status again; exit 0 only if every card has its netdevs.

## When the reset cannot help: firmware that never boots

Measured on 2026-10-05 on card 49:00 after the thermal halt: `mlxfwreset
--level 3` completed, the PCIe link retrained at x16, memory decoding and bus
mastering were enabled, yet the driver's re-probe logged
`Waiting for FW initialization ... (0xffffffff)` for 120 s, then
`Firmware over 120000 MS in pre-initializing state, aborting` and
`firmware version: 65535.65535.65535`. Every BAR read returned all-ones: the
firmware stays in its protected post-thermal state until the card loses power.
The tool recognises those kernel lines after a failed wait, says so, and skips
the PCI route (which would re-probe the same dead firmware). The fix is a real
power cycle: `sudo poweroff`, wait 30 s so PCIe standby power drains, power on.
A soft reboot is not enough.

Could the card alone be power cycled? Not on pancake0. The two ways Linux can
cut power to one slot are a PCIe hotplug slot power controller
(`/sys/bus/pci/slots/<n>/power`) and ACPI power resources on the root port
(D3cold). Checked 2026-10-05 on the Pro WS WRX80E-SAGE SE WIFI: the slots
directory is empty and the root ports at 40:03.1 and 60:01.1 have ACPI nodes
without `_PR0`/`_PR3` resources, so neither exists; the HHHL ConnectX-7 has no
auxiliary power connector either. Every reset the host can issue is logical
(FLR, secondary bus reset, link retrain) and the thermal latch survives them.
Hence the monitor's job is to catch the `temp_warn` line in the ~90 s before
the firmware latches, not to recover afterwards.

Afterwards run `./reboot_cams.sh` if the cameras need a power cycle and lens
check, and `./check_kernel_tuning.py` to confirm the mlx5 interrupt affinity
survived the re-probe.

## Running without a sudo password

The tool itself needs root only for `--reset`. The repo already uses a
root-owned copy plus a `sudoers.d` rule for the read-only inventory probe
(`install_root_probe.sh`); the same pattern fits here: install a root-owned
copy of `reset_camera_nics.py`, `hostconfig.py` and a snapshot of the host
config under `/usr/local/libexec/pancakebatter/`, and allow exactly that
command for the rig user. Keep the rule pointed at the root-owned copy, never
at the checkout, so editing the repo does not change what runs as root.

## Checking the cards once they are back

```bash
./reset_camera_nics.py                       # netdevs, carrier, health messages
sudo mget_temp -d 61:00.0                    # ASIC temperature (firmware cutoff ~105 C)
sensors | grep -A2 mlx5                      # the same through hwmon, no root needed
./rig_health_check.py                        # everything in one go
```
