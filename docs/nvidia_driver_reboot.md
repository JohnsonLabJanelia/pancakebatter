# Driver 610 reboot and return to the desktop

Completed, 2026-09-19: the machine rebooted, the desktop was restored, and
host/CUDA, camera GPUDirect, headless TensorRT/NVENC, and GUI recording checks
passed. See the [runtime acceptance record](nvidia_driver_runtime_acceptance_20260919.md).
The console procedure below records the completed migration; it is not a
request for another reboot. Toolkit-only CUDA 13.1 staging is a separate step.

## Historical pre-reboot state, 2026-09-19

The authorized 535-to-610 replacement completed. The root apply helper reports
`ready_for_reboot` in
`/mnt/Data2/nvenc-driver-console-entry-20260919T215605Z-lrljphtf/apply-status.json`.
An independent read-only review verified all 22 exact package versions and
architectures, clean dpkg status, five 610.57.04 DKMS modules for
6.5.0-44-generic, and peer-memory dependencies on `nvidia` and `ib_core`.
The OFED registrations remain installed, openibd is active, CUDA still selects
12.2, and TensorRT 10.0.1.6 remains present. NVIDIA modules are unloaded and GDM
is inactive. The existing X configuration was retained. Runtime acceptance is
still pending; no reboot has been performed by the agent.

## Console boot

The current GRUB configuration hides its menu with a zero-second timeout.
Use a temporary change of the default systemd target instead of trying to catch
GRUB and edit a one-time kernel argument. This supersedes that step in the
original frozen maintenance document. It does not change GRUB or the kernel.
The target remains text mode across reboots until explicitly restored below.

From the authenticated root shell on tty4:

```bash
systemctl set-default multi-user.target && systemctl reboot
```

After boot, log in on a text console and reopen the existing Codex session.
Leave GDM stopped while reviewing GPU, driver, OFED, and peer-memory health.
The post-boot target expectation is now `multi-user.target`; the rest of the
post-reboot checks in `nvidia_driver_maintenance.md` still apply. Confirm all
nine GPU UUIDs/PCI addresses against the saved baseline and driver 610.57.04.

## Restore desktop boot after host checks pass

From an authenticated root console:

```bash
systemctl set-default graphical.target
systemctl get-default
systemctl start gdm3.service
systemctl is-active gdm3.service
```

Expect `graphical.target` and `active`. Setting the default controls future
boots; starting GDM brings up the desktop in this boot. Continue with the EVT
GPUDirect smoke, two-camera PTP/YOLO/NVENC acceptance, and GUI validation in the
maintenance runbook before declaring production compatibility.

If host checks fail, keep the console boot target in place and inspect the
saved evidence before using the documented rollback. The frozen package bundle
and rollback installer on Data2 have not changed.
