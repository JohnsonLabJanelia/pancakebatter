# Power-cycle runbook (first used 2026-10-05, after the ConnectX thermal halt)

When to use: a camera NIC card has thermally latched (`reset_camera_nics.py`
shows a card with no netdevs and `mlxfwreset` cannot bring it back), or the
host otherwise needs a cold start. Background: [`camera_nic_reset.md`](camera_nic_reset.md),
[`rig_services_at_boot.md`](rig_services_at_boot.md), [`time_and_clocks.md`](time_and_clocks.md).

Two rules:

- **Power off, do not reboot.** A warm reboot keeps standby power on the PCIe
  slots and the NIC firmware stays latched. `sudo poweroff`, wait 30 s after
  the fans stop, press the power button.
- **Nothing may be recording** during any of this. The NTP step jumps the
  clock, and the camera clocks follow it.

## 1. Before powering off

One-time setup that should be in place before the cold start, so everything
comes back on its own and the machine boots with the right time (timesyncd
writes the corrected time to the hardware clock once it has synchronized).

```bash
cd ~/pancakebatter
sudo timedatectl set-ntp true
timedatectl                              # wait for "System clock synchronized: yes"
sudo ./install_ptp_units.sh              # enables ptp4l/phc2sys for boot; leaves a running ptp4l alone
sudo ./install_rig_health_timer.sh
sudoedit /etc/default/rig-health         # RIG_HEALTH_EMAIL=<you>
./rig_health_check.py --test-mail <you>  # one message through the same path alerts use
```

On a machine where this is already installed, step 1 is just: confirm Orange
is not running (`pgrep -x orange` prints nothing).

## 2. Power cycle

```bash
sudo poweroff
```

Wait 30 seconds after the fans stop. Power on with the button.

## 3. After boot

```bash
cd ~/pancakebatter
./reset_camera_nics.py                   # every card OK, all mlnx netdevs present with link
systemctl status ptp4l phc2sys --no-pager
timedatectl                              # System clock synchronized: yes
./check_kernel_tuning.py                 # isolation, sysctl, THP, MTU and mlx5 IRQ affinity still match the config
systemctl list-timers rig-health.timer   # next run scheduled
./rig_health_check.py                    # camera_nics, ptp, time_sync ok (disk_space warnings are real and separate)
./reboot_cams.sh                         # before the next recording: cameras resync to PTP, lens mounts verified
```

Expect one recovery mail from the health timer on its first run: the stored
state from before the power-off has the cards missing and PTP critical, and
the transition to ok is what triggers the message.

## If the cards are still missing after a true power-off

The firmware latch is cleared by removing power, so a card that is still
absent after this points at the card itself (or its slot) rather than the
thermal state. Keep `./reset_camera_nics.py --json` output and the kernel log
from that boot (`journalctl -k -b --no-pager | grep -i mlx5`); try the other
slot before declaring the card dead; then it is a hardware conversation.
