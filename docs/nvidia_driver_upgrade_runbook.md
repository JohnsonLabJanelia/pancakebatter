# NVIDIA Driver Package-Migration Runbook

## Current execution status, 2026-09-19

The frozen 22-package set, including proprietary driver 610.57.04 and DKMS
3.2.1, is installed and the machine has rebooted. GPU inventory, peer-memory
module loading, and basic CUDA 12.2 execution passed on all nine GPUs. Desktop
restoration, EVT GPUDirect, two-camera headless real TensorRT/NVENC, and GUI
recording acceptance also passed. See the
[runtime acceptance record](nvidia_driver_runtime_acceptance_20260919.md). The planner
description and 535 baseline below document preparation; do not rerun the
old-baseline planner or apply helper as a post-upgrade check.


## Scope and status

This runbook prepares a future migration of `pancake0` from its NVIDIA `.run`
installation to exact NVIDIA packages. It does not authorize or perform the
migration. The planner has no installation mode: it cannot install or remove
packages, alter DKMS, stop services, change boot state, or reboot. Every report
sets `installation_ready = false`; a clean result means `review_required`.

The research target is proprietary driver 610.57.04 for an isolated CUDA 13.1
native-NV12 probe. A resolved transaction proves package feasibility only. It
does not prove Orange, NVENC, TensorRT, EVT GPUDirect, Rivermax, or camera
compatibility.

## Frozen production baseline

| Component | Required value |
| --- | --- |
| OS / kernel | Ubuntu 22.04 / `6.5.0-44-generic` |
| NVIDIA driver | 535.183.06 proprietary, NVIDIA `.run` owned |
| Production CUDA | 12.2; `/usr/local/cuda` continues to select it |
| TensorRT | 10.0.1.6 |
| EVT / OFED | EVT 2.55.02 / MLNX_OFED 24.01 |
| GPUDirect bridge | `nvidia_peermem` for the running kernel and OFED |
| GPUs | one RTX A6000 and eight A16 devices |

The target manifest is `configs/nvidia_driver_upgrade.pancake0.json`. It pins
the proprietary 610.57.04 package set, DKMS 3.2.1 candidate, NVIDIA framework
packages, and reviewed dependencies. Unversioned `cuda`, "latest", and open
kernel-module alternatives are outside this plan.

## Ownership and known blockers

The driver is owned by `nvidia-installer` even though its modules are registered
with DKMS. DKMS builds registered sources; it does not independently choose the
driver version. Multiple registered sources or mixed runfile/package ownership
can put an unexpected module into the active tree.

A future maintenance phase must remove the runfile-owned driver before APT
installs the reviewed transaction. The planner deliberately performs neither
step. The orphan `nvidia-fs/2.17.5` registration was backed up and quarantined
on 2026-09-19. Global DKMS status is now clean; the active NVIDIA and OFED module
files are unchanged. The completed cleanup evidence is in
`/mnt/Data2/nvenc-dkms-cleanup-20260919/cleanup.json`.

The host has DKMS 2.8.7. DKMS 3.2.1 is acceptable only when authenticated
metadata offers the exact manifest version and the simulated transaction
selects it. Do not upgrade DKMS independently.

## Planner use

Run an offline host and recovery audit:

```bash
python3 nvidia_driver_upgrade.py \
  --manifest configs/nvidia_driver_upgrade.pancake0.json
```

Without `--output-dir`, the tool prints a summary from a private temporary
workspace and then removes the detailed artifacts. Preserve a report in a new
directory that does not already exist. Offline runs deliberately block APT
readiness because the new private workspace has no NVIDIA metadata or key:

```bash
python3 nvidia_driver_upgrade.py \
  --manifest configs/nvidia_driver_upgrade.pancake0.json \
  --output-dir /tmp/pancake0-driver-plan-review
```

Refresh authenticated NVIDIA metadata and simulate APT only by explicit opt-in:

```bash
python3 nvidia_driver_upgrade.py \
  --manifest configs/nvidia_driver_upgrade.pancake0.json \
  --output-dir /tmp/pancake0-driver-plan-refresh \
  --refresh-metadata
```

Refresh requires a new `--output-dir`. It verifies the pinned NVIDIA key,
fetches NVIDIA metadata into that mode-0700 directory, seeds isolated APT state
from the host's existing official Ubuntu metadata, and runs
`apt-get --simulate`. It does not refresh Ubuntu metadata, download `.deb`
payloads, modify host APT state, mutate DKMS, or change packages or services.

The reviewed refresh resolved 22 transitions with install recommendations
disabled. `screen-resolution-extra=0.18.2` remains an explicitly pinned optional
dependency, but it, `nvidia-settings`, and `libxnvctrl0` were not selected. Any
unreviewed package or different version blocks the plan for review.

Recovery checks establish the Data2 filesystem identity, Clonezilla metadata,
source serial, required image-file names and nonzero sizes, and the recorded
image-check attestation. They do not read the entire image or prove restoration.
The matching 535.183.06 installer is preserved under
`/mnt/Data2/nvenc-session-recovery-20260919`. Its SHA-256 is pinned in the manifest
and matches Google's independently published gVisor checksum. A commit-pinned
Google COS manifest also matches its SHA-512, BLAKE2B and 341,920,517-byte size.
`rollback-verification.json` beside the installer records the NVIDIA download
URL, checksum sources and verification evidence. The installer has not run.

The one-time cleanup completed successfully through the user's authenticated
terminal. The guarded script is preserved at
`/mnt/Data2/nvenc-session-recovery-20260919/quarantine_stale_gds.py` for audit.
It first verifies the missing source, absent loaded/current-kernel GDS module,
old-kernel-only registration, and absence of an installed GDS package. It
archives and verifies the entire registration to
`/mnt/Data2/nvenc-dkms-cleanup-20260919`, then atomically moves it to
`/var/lib/dkms-quarantine/nvidia-fs-2.17.5-20260919`. It records before/after
DKMS status and verifies unchanged NVIDIA/peer-memory/OFED module files and
dpkg status. It does not run DKMS removal, depmod, initramfs changes, or module
unloading. The CUDA 12.2 cuFile/GDS user-space files remain in place.

The post-cleanup planner report is
`/mnt/Data2/nvenc-driver-plan-preflight-clean-20260919/summary.txt`. It exits 0
with `review_required` and zero blockers: all nine GPUs, the loaded 535 driver,
peer-memory bridge, kernel/OFED/CUDA/TensorRT baseline, rollback installer, and
22-transition package simulation pass the planning checks. This does not mark
the new driver installed or qualified. The target payloads are now downloaded
and verified in `/mnt/Data2/nvenc-driver-packages-20260919`, with the previous
DKMS package isolated under `packages/rollback/`. The exact 22-file offline
simulation matches the reviewed plan. See `nvidia_driver_maintenance.md` for
the prepared operator sequence and its console and runtime acceptance gates.

The moved registration and Data2 tar archive preserve the original state;
restoring it would restore the original broken DKMS entry, not GDS functionality.

## Reproducible package download

`nvidia_driver_prefetch.py` is a download-only companion to the planner. It
requires a zero-blocker saved plan, checks the artifact hashes and transaction,
reverifies the NVIDIA and Ubuntu repository signatures and package-index hashes,
and checks each downloaded file's size, SHA-256, architecture, name and version.
It never installs packages. The old DKMS package is a separate rollback payload.
For a new download directory, use:

```bash
python3 nvidia_driver_prefetch.py \
  --plan-dir /mnt/Data2/nvenc-driver-plan-preflight-clean-20260919 \
  --output-dir /tmp/pancake0-driver-package-freeze
```

The preserved bundle has `package-lock.json`, `package-lock.tsv`, `SHA256SUMS`,
package files, verification evidence, supplemental exact preferences, and the
maintenance document. `nvidia_driver_package_set.md` explains all 22 packages.
The 610 source also built successfully in scratch against the existing 6.5
kernel and OFED headers; the resulting peer-memory module links to `ib_core`.
That build was not installed or registered with DKMS and is not runtime testing.

The native package profile does not preserve the old runfile's 32-bit libraries
or NVIDIA settings/xconfig utilities. Jeremy confirmed no known 32-bit GPU
applications on 2026-09-19; proceed with the native profile. It preserves the
64-bit compute/video/display stack. The optional settings/xconfig utilities
remain outside this transaction.

## Gates before a future maintenance window

Require the generated evidence to show:

1. Kernel and headers remain exactly `6.5.0-44-generic`.
2. Module, user-library, DKMS, and runfile records agree on 535.183.06; no APT
   NVIDIA driver already owns the installation.
3. All nine expected GPU UUIDs are present and their PCI identities are recorded.
4. OFED 24.01 and `nvidia_peermem` match the running kernel; EVT and Rivermax
   are preserved.
5. `/usr/local/cuda` resolves to CUDA 12.2 and TensorRT remains 10.0.1.6.
6. Signed metadata resolves the exact 610.57.04, DKMS, framework, and dependency
   versions, with the reviewed 22-transition set and no kernel, open-module,
   CUDA, TensorRT, OFED, or EVT replacement.
7. DKMS is clean, including separately reviewed resolution of stale `nvidia-fs`.
8. The Data2 recovery folder and matching 535 installer with verified SHA-256
   are readable.

## Ordered future phase

After successful console entry, [nvidia_driver_console_apply.md](nvidia_driver_console_apply.md)
describes the root helper for the frozen driver replacement and pre-reboot checks.

For the initial root-console backup and desktop shutdown, see
[nvidia_driver_console_entry.md](nvidia_driver_console_entry.md). Its helper
stops before module removal; continue the reviewed maintenance procedure after
inspecting the saved status.

This is a review checklist, not an executable procedure:

1. Freeze the manifest, metadata, simulation, report, and rollback evidence.
   Use a separately reviewed prefetch step for exact `.deb` files and checksums;
   this planner does not download them.
2. Enter a scheduled console window and stop every GPU and camera client.
3. Unload `nvidia_peermem` while preserving OFED, EVT, Rivermax, the kernel,
   CUDA, TensorRT, and camera-network configuration.
4. Remove the runfile-owned 535 driver with its matching NVIDIA uninstaller.
5. Install only the frozen proprietary 610.57.04 transaction.
6. Confirm the same kernel remains selected, then reboot once.
7. Run host verification before starting cameras or Orange. If it fails, stop
   and use the reviewed package rollback or Data2 recovery route.

Host verification must establish kernel/driver and user-library parity,
proprietary module flavor, DKMS state, nine GPUs, NVENC/EGL, unchanged CUDA and
TensorRT paths, unchanged OFED, and matching `nvidia_peermem`. Only then run
one-camera EVT GPUDirect streaming, multi-camera PTP recording with zero frame
gaps/encode failures, and Orange YOLO/TensorRT/NVENC and GUI checks.

Keep CUDA 13.1 at an explicit isolated path; never repoint `/usr/local/cuda`.
Do not update the baseline until every post-reboot and camera gate passes.

## Development checks

Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`.
The current suite has 59 tests covering host mismatches, package-policy
rejections, incomplete simulations, recovery evidence, and CLI boundaries.
The planner exits `2` for blockers and `0` for a reviewable plan; neither
means installation or runtime qualification is complete. Saved outputs include
`report.json`, `summary.txt`, `packages.tsv`, the input manifest, checksums,
and private APT evidence. A normal user needs GPU-device visibility for the
full host audit; a restricted sandbox may report an inventory blocker.

## References

- [NVIDIA Driver Installation Guide](https://docs.nvidia.com/datacenter/tesla/driver-installation-guide/)
- [NVIDIA CUDA Installation Guide for Linux](https://docs.nvidia.com/cuda/cuda-installation-guide-linux/)
- [Ubuntu Server NVIDIA driver guidance](https://documentation.ubuntu.com/server/how-to/graphics/install-nvidia-drivers/)
