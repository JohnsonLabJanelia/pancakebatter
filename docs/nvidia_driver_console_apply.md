# Apply the frozen driver migration from the root console

The preceding console-entry stage completed successfully on 2026-09-19:

- Evidence: `/mnt/Data2/nvenc-driver-console-entry-20260919T215605Z-lrljphtf`.
- The 1,102,755,840-byte live-state backup compared successfully against its
  source files and has SHA-256
  `7e4478f43a611a4d7d8e511f4c7a0fb1a7ad6394aa404539742457aaaa35dabd`.
- GDM and APT automation are inactive, OFED is active, no GPU clients remain,
  and driver 535 is still installed and loaded.
- Codex runs on tty3; the authenticated root shell on tty4 is inside sudo's
  `/dev/pts/4`. The helper verifies its kernel process ancestry to the tty4 login.

From that root shell:

```bash
cd /home/jeremy/pancakebatter
/usr/bin/python3 nvidia_driver_apply.py --apply
```

This is the actual driver replacement. The helper imports the adjacent
`nvidia_driver_enter_console.py`; keep those repository files together.
It verifies the frozen bundle, rollback installer, exact successful root-owned
console-entry status, and live-state archive hash. It rechecks inactive services,
no GPU clients, and package-manager inactivity. It refuses a second apply
attempt while an `apply-status.json` already exists; inspect a failed attempt
before deciding how to proceed.

It copies the 22 verified target packages into a fresh root-owned private APT
cache and verifies a simulation before unloading anything. It unloads only the
five NVIDIA modules, in dependency order, without force. It then runs:

```text
nvidia-uninstall --ui=none --log-file-name=<evidence>/nvidia-uninstall-535.183.06.log
```

The uninstaller remains interactive. Confirm removal of the existing NVIDIA
driver. It saves its own log in the evidence directory. Successful removal must
leave no old installer/uninstaller, 535 source or registration, or old NVIDIA
module files. The non-NVIDIA DKMS registrations must match the saved baseline.

The helper repeats the exact 22-package simulation after uninstall. It creates
a runtime mask for `nvidia-persistenced`, installs only the copied offline files
with recommendations disabled, and supplies yes for that already-reviewed
transaction. Existing conffiles are preserved with `--force-confold`. It never
runs repository refresh, fix-broken, autoremove, or an unversioned driver install.
Normal APT/dpkg locking remains enabled for the real install.

Before reporting success it checks all 22 installed versions/architectures,
clean dpkg/DKMS status, all five 610 modules for kernel 6.5.0-44, proprietary
module flavor, peer-memory linkage to `ib_core`, preserved non-NVIDIA DKMS
registrations and OFED package/service, initramfs module versions, CUDA/TRT
paths, the saved graphical boot default, disabled DKMS boot autoinstall service,
the persistence runtime mask, and absence of loaded NVIDIA modules.

It writes an atomic, readable `apply-status.json` beside the existing
`status.json`. Detailed package, uninstall, simulation, and module evidence
remains private to root. A failed stage is recorded and left running for review;
there is no automatic repair, rollback, desktop start, module load, or reboot.
Return to Codex on tty3 when it prints either `STOPPED` or
`INSTALL CHECKS PASSED — READY FOR REBOOT REVIEW`.

Reboot and post-boot acceptance remain separate steps in
`nvidia_driver_maintenance.md`. The checked rollback procedure and offline 535
installer are available if the migration or runtime acceptance fails.

Preparation validation: the apply helper's own copied-cache offline simulation
matched 22 install and 22 configure transitions. Eleven new tests cover exact
versions/architectures, unexpected removals/configurations, incomplete plans,
the root/console/GPU-use boundaries, and refusal to uninstall after a failed
simulation or module unload. This validation did not run `--apply` against the host.
