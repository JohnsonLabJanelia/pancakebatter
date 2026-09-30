# Pancake0 NVIDIA 535-to-610 Maintenance Runbook

## Status

This document prepares commands; it does not authorize or perform maintenance.
Once Jeremy approves a console maintenance window, the sequence is one
fail-closed operation and does not need repeated approval between its reviewed
steps. Jeremy must authenticate `sudo` himself from a local or independently
reachable text console. Stopping GDM closes the desktop and this chat.

The intended result is proprietary driver 610.57.04 on the unchanged
`6.5.0-44-generic` kernel. `/usr/local/cuda` must remain CUDA 12.2, TensorRT
must remain 10.0.1.6, and MLNX_OFED must remain 24.01. No CUDA toolkit, kernel,
OFED, EVT, Rivermax, or camera-network package belongs in the transaction.

The native target has 22 transitions: 21 new driver/framework packages and
DKMS `2.8.7-2ubuntu2 -> 1:3.2.1-1ubuntu1`. Recommendations are disabled, so
`nvidia-settings`, `libxnvctrl0`, and `screen-resolution-extra` are absent.
The runfile's `nvidia-settings`/`nvidia-xconfig` tools will also disappear.

Jeremy confirmed on 2026-09-19 that there are no known 32-bit GPU applications.
Proceed with the native 22-package profile. The omitted optional NVIDIA
settings/xconfig utilities are documented scope limitations; the Xorg and
64-bit graphics libraries remain in the target. If those utilities become
necessary, review their exact dependency closure separately.

The current 535 runfile installed unowned libraries in
`/usr/lib/i386-linux-gnu`. The attempted i386 closure is incomplete because the
matching `libdrm2:i386` is unavailable from the current mirror/version state.
This is a qualification boundary, not evidence that an NVIDIA i386 package is
missing.

The completed durable bundle is the sole package source for this procedure. Its
top-level `SHA256SUMS` covers the locks, exact preference file, packages, and
scratch-build/offline-APT evidence; verify that checksum set on Data2 before
entering the maintenance window.

## Frozen inputs

The durable bundle is `/mnt/Data2/nvenc-driver-packages-20260919`:

- `packages/target/`: exactly 22 target `.deb` files;
- `packages/rollback/dkms_2.8.7-2ubuntu2_all.deb`: separate rollback DKMS;
- `package-lock.json` and `package-lock.tsv`: package, version, architecture,
  relative path, size, SHA-256, and source URL;
- `SHA256SUMS`, `prefetch-report.json`, the approved simulation, and
  `nvenc-driver-610-exact.pref` before execution;
- `kernel-build.log` and `kernel-build-verification.json`: the scratch build
  that compiled all five 610 modules for 6.5.0-44 without installing or
  registering them. It proved `nvidia_peermem` version 610.57.04, dependency
  `nvidia,ib_core`, and the direct
  `/usr/src/ofa_kernel/x86_64/6.5.0-44-generic/include` path.

Never glob the parent `packages/` directory: that would mix both DKMS versions.
Never enable a global CUDA/NVIDIA repository or install an unversioned latest
driver. The verified offline resolver uses all 22 explicit local paths, empty
source/list/preference state, a private cache containing copies of those 22
files, `--no-download`, `--no-remove`, and `--no-install-recommends`.

Rollback also requires:

- `/mnt/Data2/nvenc-session-recovery-20260919/NVIDIA-Linux-x86_64-535.183.06.run`;
- SHA-256 `c7bb0a0569c5347845479ed4e3e4d885c6ee3b8adf068c3401cdf754d5ba3d3b`;
- the checked Clonezilla image and recorded source-disk serial.

## Preflight before approval

These commands are read-only:

```bash
set -Eeuo pipefail
BUNDLE=/mnt/Data2/nvenc-driver-packages-20260919
RECOVERY=/mnt/Data2/nvenc-session-recovery-20260919
RUNFILE="$RECOVERY/NVIDIA-Linux-x86_64-535.183.06.run"

test "$(uname -r)" = 6.5.0-44-generic
test "$(readlink -f /usr/local/cuda)" = /usr/local/cuda-12.2
test -d /usr/local/TensorRT-10.0.1.6
test -x /usr/bin/gcc-12
test -d /usr/src/linux-headers-6.5.0-44-generic
test -s /usr/src/ofa_kernel/x86_64/6.5.0-44-generic/Module.symvers
grep -w ib_register_peer_memory_client \
  /usr/src/ofa_kernel/x86_64/6.5.0-44-generic/Module.symvers
grep -w ib_unregister_peer_memory_client \
  /usr/src/ofa_kernel/x86_64/6.5.0-44-generic/Module.symvers

printf '%s  %s\n' \
  c7bb0a0569c5347845479ed4e3e4d885c6ee3b8adf068c3401cdf754d5ba3d3b \
  "$RUNFILE" | sha256sum -c -

cd "$BUNDLE"
sha256sum -c SHA256SUMS
test "$(awk -F '\t' 'NR>1 && $1=="target" {n++} END {print n+0}' package-lock.tsv)" -eq 22
test "$(awk -F '\t' 'NR>1 && $1=="rollback" {n++} END {print n+0}' package-lock.tsv)" -eq 1

tail -n +2 package-lock.tsv |
while IFS=$'\t' read -r set_name package version architecture relative size digest url; do
  [[ "$relative" == packages/target/*.deb || "$relative" == packages/rollback/*.deb ]]
  [[ "$relative" != *..* ]]
  file="$BUNDLE/$relative"
  test -f "$file"
  test "$(stat -c %s "$file")" = "$size"
  test "$(sha256sum "$file" | cut -d' ' -f1)" = "$digest"
  test "$(dpkg-deb -f "$file" Package)" = "$package"
  test "$(dpkg-deb -f "$file" Version)" = "$version"
  test "$(dpkg-deb -f "$file" Architecture)" = "$architecture"
done

systemctl get-default
readlink -f /etc/systemd/system/display-manager.service
nvidia-smi --query-gpu=uuid,index,name,driver_version,pci.bus_id --format=csv,noheader
dkms status
```

Require `graphical.target`, `gdm3.service`, nine expected GPU UUIDs, current
driver 535.183.06, and clean DKMS output. The stale `nvidia-fs/2.17.5`
registration has already been quarantined. Verify it; never rerun cleanup:

```bash
test ! -e /var/lib/dkms/nvidia-fs
test -s /mnt/Data2/nvenc-dkms-cleanup-20260919/cleanup.json
printf '%s  %s\n' \
  485c262e12808c4e86310950ae0aa65d0879be554288d98b9139c95f1d6c5f95 \
  /mnt/Data2/nvenc-dkms-cleanup-20260919/nvidia-fs-registration.tar.gz \
  | sha256sum -c -
```

Do not trust `/usr/src/ofa_kernel/default`; it points at an old 5.19 tree. The
610 build log and module must show the explicit 6.5.0-44 OFED path.

## Approved console window

### 1. Enter, save state, and quiesce

Log in on a text console, then:

```bash
sudo -v
sudo -i
set -Eeuo pipefail
export LC_ALL=C LANG=C PATH=/usr/sbin:/usr/bin:/sbin:/bin

BUNDLE=/mnt/Data2/nvenc-driver-packages-20260919
RECOVERY=/mnt/Data2/nvenc-session-recovery-20260919
RUNFILE="$RECOVERY/NVIDIA-Linux-x86_64-535.183.06.run"
EVIDENCE="/mnt/Data2/nvenc-driver-maintenance-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$EVIDENCE"

systemctl get-default | tee "$EVIDENCE/default-target.txt"
test "$(cat "$EVIDENCE/default-target.txt")" = graphical.target
nvidia-smi --query-gpu=uuid,index,name,driver_version,pci.bus_id \
  --format=csv,noheader >"$EVIDENCE/gpu-before.csv"
cp "$BUNDLE/package-lock.json" "$BUNDLE/package-lock.tsv" \
  "$BUNDLE/SHA256SUMS" "$EVIDENCE/"

BACKUP_PATHS=(var/lib/dkms boot)
test -d /var/lib/nvidia && BACKUP_PATHS+=(var/lib/nvidia)
test -d /etc/dkms && BACKUP_PATHS+=(etc/dkms)
tar --acls --xattrs --numeric-owner -C / -cpf \
  "$EVIDENCE/driver-state-before.tar" "${BACKUP_PATHS[@]}"
sha256sum "$EVIDENCE/driver-state-before.tar" \
  >"$EVIDENCE/driver-state-before.tar.sha256"
cp -a /var/log/nvidia-installer.log "$EVIDENCE/" 2>/dev/null || true
```

Stop Orange, recorders, camera tools, notebooks, and other GPU clients through
their owners. Then stop the desktop and package automation:

```bash
systemctl isolate multi-user.target
systemctl stop apt-daily.timer apt-daily-upgrade.timer \
  apt-daily.service apt-daily-upgrade.service unattended-upgrades.service
test "$(systemctl is-active gdm3.service || true)" = inactive

if nvidia-smi --query-compute-apps=pid,process_name \
     --format=csv,noheader,nounits | grep -q '[^[:space:]]'; then
  echo 'GPU compute client remains; abort.' >&2; exit 1
fi
GPU_USERS="$(fuser /dev/nvidia* /dev/nvidia-caps/* 2>/dev/null || true)"
if [[ -n "$GPU_USERS" ]]; then
  fuser -v /dev/nvidia* /dev/nvidia-caps/* || true
  echo 'NVIDIA device user remains; abort.' >&2; exit 1
fi
```

Leave `openibd`, `mlx5_core`, and `ib_core` alone.

### 2. Unload 535 and remove runfile ownership

```bash
for module in nvidia_peermem nvidia_uvm nvidia_drm nvidia_modeset nvidia; do
  if grep -qw "$module" /proc/modules; then
    modprobe -r "$module"
  fi
done
if grep -Eq '^(nvidia|nvidia_peermem|nvidia_uvm|nvidia_drm|nvidia_modeset) ' /proc/modules; then
  echo 'NVIDIA module remains; abort without forcing unload.' >&2; exit 1
fi

test -x /usr/bin/nvidia-uninstall
/usr/bin/nvidia-uninstall
cp -a /var/log/nvidia-installer.log "$EVIDENCE/nvidia-uninstall.log"
test ! -e /usr/src/nvidia-535.183.06
test ! -e /usr/bin/nvidia-uninstall
if dkms status -m nvidia -v 535.183.06 2>&1 | grep -q 535.183.06; then
  echo '535 DKMS registration remains; abort.' >&2; exit 1
fi
test "$(readlink -f /usr/local/cuda)" = /usr/local/cuda-12.2
test -d /usr/local/TensorRT-10.0.1.6
```

Never force module removal. If a VT/framebuffer keeps `nvidia_drm` busy, stop
before uninstalling and reboot with this one-time GRUB addition, then resume:

```text
systemd.unit=multi-user.target modprobe.blacklist=nvidia,nvidia_drm,nvidia_modeset,nvidia_uvm,nvidia_peermem
```

Do not change the saved default target.

### 3. Simulate and install the frozen local transaction

Build the arrays only from target rows and copy the 22 files into a private
archive cache. The copy is intentional: pointing APT at an empty archive cache
failed, while the copied-cache simulation produced the exact approved 22
transitions without modifying the bundle.

```bash
APT_ROOT="$EVIDENCE/offline-apt"
APT_ARCHIVES="$APT_ROOT/cache/archives"
install -d -m 0700 "$APT_ROOT/apt.conf.d" "$APT_ROOT/sources.list.d" \
  "$APT_ROOT/preferences.d" "$APT_ROOT/lists/partial" \
  "$APT_ARCHIVES/partial" "$APT_ROOT/logs"
: >"$APT_ROOT/apt-main.conf"
: >"$APT_ROOT/sources.list"
: >"$APT_ROOT/preferences"
printf 'Dir::Etc::main "%s";\nDir::Etc::parts "%s";\n' \
  "$APT_ROOT/apt-main.conf" "$APT_ROOT/apt.conf.d" \
  >"$APT_ROOT/apt-bootstrap.conf"

awk -F '\t' 'NR>1 && $1=="target" {print $5}' "$BUNDLE/package-lock.tsv" \
  >"$APT_ROOT/target-paths.txt"
mapfile -t TARGET_PATHS <"$APT_ROOT/target-paths.txt"
test "${#TARGET_PATHS[@]}" -eq 22
test "$(sort -u "$APT_ROOT/target-paths.txt" | wc -l)" -eq 22
TARGET_DEBS=()
for relative in "${TARGET_PATHS[@]}"; do
  [[ "$relative" == packages/target/*.deb && "$relative" != *..* ]]
  source_deb="$BUNDLE/$relative"
  test -f "$source_deb"
  cp --reflink=never "$source_deb" "$APT_ARCHIVES/$(basename "$source_deb")"
  TARGET_DEBS+=("$source_deb")
done

APT_OFFLINE=(
  -o "Dir::Etc::sourcelist=$APT_ROOT/sources.list"
  -o "Dir::Etc::sourceparts=$APT_ROOT/sources.list.d"
  -o "Dir::Etc::preferences=$APT_ROOT/preferences"
  -o "Dir::Etc::preferencesparts=$APT_ROOT/preferences.d"
  -o "Dir::State::lists=$APT_ROOT/lists"
  -o "Dir::State::status=/var/lib/dpkg/status"
  -o "Dir::Cache=$APT_ROOT/cache"
  -o "Dir::Cache::archives=$APT_ARCHIVES"
  -o "Dir::Log=$APT_ROOT/logs"
)

APT_CONFIG="$APT_ROOT/apt-bootstrap.conf" CC=/usr/bin/gcc-12 \
apt-get "${APT_OFFLINE[@]}" --simulate --no-download --no-remove \
  --no-install-recommends install "${TARGET_DEBS[@]}" \
  | tee "$EVIDENCE/install.simulate.txt"

test "$(grep -c '^Inst ' "$EVIDENCE/install.simulate.txt")" -eq 22
if grep -Eq '^(Remv|Purg) ' "$EVIDENCE/install.simulate.txt"; then
  echo 'Unexpected removal in install simulation; abort.' >&2; exit 1
fi
awk -F '\t' 'NR>1 && $1=="target" {print $2}' "$BUNDLE/package-lock.tsv" | sort \
  >"$APT_ROOT/expected-packages.txt"
awk '/^Inst / {sub(/:.*/, "", $2); print $2}' "$EVIDENCE/install.simulate.txt" | sort \
  >"$APT_ROOT/simulated-packages.txt"
diff -u "$APT_ROOT/expected-packages.txt" "$APT_ROOT/simulated-packages.txt"

awk -F '\t' 'NR>1 && $1=="target" {print $2 "\t" $3 "\t" $4}' \
  "$BUNDLE/package-lock.tsv" | sort >"$APT_ROOT/expected-package-versions.tsv"
python3 - "$EVIDENCE/install.simulate.txt" >"$APT_ROOT/simulated-package-versions.tsv" <<'PY'
import re, sys
rows = []
pattern = re.compile(r'^Inst\s+(\S+?)(?::\S+)?(?:\s+\[[^]]+\])?\s+\((\S+).*\s+\[([^]]+)\]\)$')
for line in open(sys.argv[1], encoding='utf-8'):
    match = pattern.match(line.rstrip())
    if match:
        rows.append('\t'.join(match.groups()))
print('\n'.join(sorted(rows)))
PY
diff -u "$APT_ROOT/expected-package-versions.tsv" \
  "$APT_ROOT/simulated-package-versions.tsv"
```

The `nvidia-dkms` preinst can call a surviving `/usr/bin/nvidia-uninstall -s`
and ignore its failure; the explicit uninstall and absence checks above are
therefore mandatory. Runtime-mask only `nvidia-persistenced` so its postinst
cannot start it before reboot, then run the same local transaction with normal
APT/dpkg locking. The runtime mask disappears at reboot.

```bash
systemctl mask --runtime nvidia-persistenced.service

DEBIAN_FRONTEND=noninteractive APT_CONFIG="$APT_ROOT/apt-bootstrap.conf" \
CC=/usr/bin/gcc-12 apt-get "${APT_OFFLINE[@]}" --no-download --no-remove \
  --no-install-recommends install "${TARGET_DEBS[@]}" \
  |& tee "$EVIDENCE/install.txt"

test "$(systemctl is-enabled nvidia-persistenced.service || true)" = masked-runtime
PREF_TARGET=/etc/apt/preferences.d/nvenc-driver-610-exact.pref
test ! -e "$PREF_TARGET"
test ! -L "$PREF_TARGET"
install -m 0644 "$BUNDLE/nvenc-driver-610-exact.pref" "$PREF_TARGET"
```

Do not run `apt update`, `apt --fix-broken install`, or `apt autoremove`. Do not
add `apt-mark hold`; rollback must remain explicit and reversible.

### 4. Pre-reboot checks

```bash
dpkg --audit | tee "$EVIDENCE/dpkg-audit.txt"
test ! -s "$EVIDENCE/dpkg-audit.txt"
test "$(dpkg-query -W -f='${Version}' dkms)" = 1:3.2.1-1ubuntu1
test "$(dpkg-query -W -f='${Version}' nvidia-driver)" = 610.57.04-1ubuntu1
test "$(dpkg-query -W -f='${Version}' cuda-drivers)" = 610.57.04-1ubuntu1
dkms status -m nvidia -v 610.57.04 | tee "$EVIDENCE/dkms-610.txt"
grep -F '6.5.0-44-generic, x86_64: installed' "$EVIDENCE/dkms-610.txt"
dkms status -m mlnx-ofed-kernel -v 24.01.OFED.24.01.0.3.3.1 \
  | grep -F '6.5.0-44-generic, x86_64: installed'
test "$(modinfo -k 6.5.0-44-generic -F version nvidia)" = 610.57.04
test "$(modinfo -k 6.5.0-44-generic -F version nvidia_peermem)" = 610.57.04
modinfo -k 6.5.0-44-generic -F filename nvidia_peermem \
  | grep -E '^/lib/modules/6[.]5[.]0-44-generic/updates/dkms/nvidia-peermem[.]ko'
modinfo -k 6.5.0-44-generic -F vermagic nvidia_peermem | grep -F 6.5.0-44-generic
PEERMEM_DEPS="$(modinfo -k 6.5.0-44-generic -F depends nvidia_peermem)"
grep -Eq '(^|,)nvidia(,|$)' <<<"$PEERMEM_DEPS"
grep -Eq '(^|,)ib_core(,|$)' <<<"$PEERMEM_DEPS"
if grep -q '^nv_peer_mem ' /proc/modules; then
  echo 'Legacy nv_peer_mem is loaded; abort.' >&2; exit 1
fi
test "$(readlink -f /usr/local/cuda)" = /usr/local/cuda-12.2
test -d /usr/local/TensorRT-10.0.1.6
test "$(dpkg-query -W -f='${Version}' mlnx-ofed-kernel-dkms)" = \
  24.01.OFED.24.01.0.3.3.1-1
INITRD_TREE="$EVIDENCE/initrd-6.5.0-44"
install -d -m 0700 "$INITRD_TREE"
unmkinitramfs /boot/initrd.img-6.5.0-44-generic "$INITRD_TREE"
mapfile -t INITRD_NVIDIA < <(find "$INITRD_TREE" -type f \
  -path '*/updates/dkms/nvidia*.ko*' -print)
for module_file in "${INITRD_NVIDIA[@]}"; do
  test "$(modinfo -F version "$module_file")" = 610.57.04
done
test "$(systemctl is-enabled dkms.service || true)" = disabled
if grep -Eq '^(nvidia|nvidia_peermem|nvidia_uvm|nvidia_drm|nvidia_modeset) ' /proc/modules; then
  echo 'NVIDIA module loaded before planned reboot; abort.' >&2; exit 1
fi
```

Do not load the new modules into the old boot. After reviewing evidence, the
operator runs `systemctl set-default multi-user.target && systemctl reboot`
from the root console. This temporarily changes the persistent default target
because the host GRUB menu is hidden with a zero-second timeout. It stays in
text mode until the explicit restoration after host checks below. No script
schedules a reboot. See [the reboot note](nvidia_driver_reboot.md).

## Post-reboot target acceptance

Authenticate again on the console. These are target expectations, not the old
535 planning baseline:

```bash
sudo -v
sudo -i
set -Eeuo pipefail
export LC_ALL=C LANG=C PATH=/usr/sbin:/usr/bin:/sbin:/bin
test "$(uname -r)" = 6.5.0-44-generic
test "$(systemctl get-default)" = multi-user.target
test "$(systemctl is-active gdm3.service || true)" = inactive
test "$(readlink -f /usr/local/cuda)" = /usr/local/cuda-12.2
test -d /usr/local/TensorRT-10.0.1.6
test "$(modinfo -F version nvidia)" = 610.57.04
test "$(modinfo -F version nvidia_peermem)" = 610.57.04
modinfo -F vermagic nvidia_peermem | grep -F 6.5.0-44-generic
PEERMEM_DEPS="$(modinfo -F depends nvidia_peermem)"
grep -Eq '(^|,)nvidia(,|$)' <<<"$PEERMEM_DEPS"
grep -Eq '(^|,)ib_core(,|$)' <<<"$PEERMEM_DEPS"
systemctl is-active --quiet openibd.service
grep -q '^ib_core ' /proc/modules
if grep -q '^nv_peer_mem ' /proc/modules; then
  echo 'Legacy nv_peer_mem is loaded; abort.' >&2; exit 1
fi
modprobe nvidia
modprobe nvidia_peermem
grep -q '^nvidia_peermem ' /proc/modules
test "$(cat /sys/module/nvidia/version)" = 610.57.04
if test -r /sys/module/nvidia_peermem/version; then
  test "$(cat /sys/module/nvidia_peermem/version)" = 610.57.04
fi

nvidia-smi --query-gpu=uuid,index,name,driver_version,pci.bus_id \
  --format=csv,noheader >/tmp/pancake0-gpu-610.csv
test "$(wc -l </tmp/pancake0-gpu-610.csv)" -eq 9
test "$(cut -d, -f4 /tmp/pancake0-gpu-610.csv | tr -d ' ' | sort -u)" = 610.57.04
dkms status -m nvidia -v 610.57.04
dkms status -m mlnx-ofed-kernel -v 24.01.OFED.24.01.0.3.3.1
dpkg --audit
ldconfig -p | grep -E 'lib(cuda|nvidia-encode|EGL_nvidia)[.]so'
```

Compare all nine UUID/PCI rows to `gpu-before.csv`. Then start GDM and run
camera acceptance in increasing scope as the normal user:

```bash
systemctl set-default graphical.target
test "$(systemctl get-default)" = graphical.target
systemctl start gdm3.service
systemctl is-active --quiet gdm3.service
exit

sudo -n /usr/local/bin/orange-evt-stream-smoke \
  --config-dir /home/jeremy/orange_data/config/local/100_cam4_ptp_fourcam \
  --serial 2010096 --gpu-direct 1 --measure-seconds 5 --buffer-count 64
```

Continue with the supervised timed headless specimen and isolated GUI inputs in
the [runtime acceptance record](nvidia_driver_runtime_acceptance_20260919.md).
That record replaces the old manual two-camera runner for the current binary:
the manual runner fails session identity validation, and the supervised run
needs an explicit positive `recording_control.record_for_seconds` to write its
session manifest. Stamp fresh artifact/socket paths and use the current
2010095 GPU 7/shards 7,8 and 2010096 GPU 5/shards 5,6 mapping.

Require zero camera gaps, GetFrame/IPC/encode failures, and drops; valid
real-content MP4s; correct PTP mode; and the established YOLO latency envelope.
Run GUI validation last. These gates passed on 2026-09-19; the linked record
contains evidence and the remaining application metadata caveats. Preserve the
frozen 535 planning/rollback baseline rather than rewriting its historical files.

## Rollback

If install or target acceptance fails, return to a one-time console boot,
quiesce users, unload NVIDIA modules without force, and keep OFED loaded.

Purge the 21 target packages while DKMS 3.2.1 is still installed; then downgrade
DKMS and reinstall the verified 535 runfile. Never reinstall 535 over 610.

```bash
set -Eeuo pipefail
export LC_ALL=C LANG=C PATH=/usr/sbin:/usr/bin:/sbin:/bin
BUNDLE=/mnt/Data2/nvenc-driver-packages-20260919
RECOVERY=/mnt/Data2/nvenc-session-recovery-20260919
RUNFILE="$RECOVERY/NVIDIA-Linux-x86_64-535.183.06.run"
EVIDENCE="/mnt/Data2/nvenc-driver-rollback-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$EVIDENCE"

RBAPT="$EVIDENCE/offline-apt"
install -d -m 0700 "$RBAPT/apt.conf.d" "$RBAPT/sources.list.d" \
  "$RBAPT/preferences.d" "$RBAPT/lists/partial" \
  "$RBAPT/cache/archives/partial" "$RBAPT/logs"
: >"$RBAPT/apt-main.conf"
: >"$RBAPT/sources.list"
: >"$RBAPT/preferences"
printf 'Dir::Etc::main "%s";\nDir::Etc::parts "%s";\n' \
  "$RBAPT/apt-main.conf" "$RBAPT/apt.conf.d" >"$RBAPT/apt-bootstrap.conf"
RB_OPTS=(
  -o "Dir::Etc::sourcelist=$RBAPT/sources.list"
  -o "Dir::Etc::sourceparts=$RBAPT/sources.list.d"
  -o "Dir::Etc::preferences=$RBAPT/preferences"
  -o "Dir::Etc::preferencesparts=$RBAPT/preferences.d"
  -o "Dir::State::lists=$RBAPT/lists"
  -o "Dir::State::status=/var/lib/dpkg/status"
  -o "Dir::Cache=$RBAPT/cache"
  -o "Dir::Cache::archives=$RBAPT/cache/archives"
  -o "Dir::Log=$RBAPT/logs"
)

mapfile -t LOCKED_TARGETS < <(awk -F '\t' \
  'NR>1 && $1=="target" && $2!="dkms" {print $2}' "$BUNDLE/package-lock.tsv")
TARGET_PACKAGES=()
for package in "${LOCKED_TARGETS[@]}"; do
  state="$(dpkg-query -W -f='${db:Status-Abbrev}' "$package" 2>/dev/null || true)"
  [[ -n "$state" && "${state:1:1}" != n ]] && TARGET_PACKAGES+=("$package")
done
printf '%s\n' "${TARGET_PACKAGES[@]}" | sort \
  >"$EVIDENCE/rollback-package-names.txt"
PREF_SOURCE="$BUNDLE/nvenc-driver-610-exact.pref"
PREF_TARGET=/etc/apt/preferences.d/nvenc-driver-610-exact.pref
if [[ -e "$PREF_TARGET" || -L "$PREF_TARGET" ]]; then
  test ! -L "$PREF_TARGET"
  test -f "$PREF_TARGET"
  test "$(sha256sum "$PREF_TARGET" | cut -d' ' -f1)" = \
    "$(sha256sum "$PREF_SOURCE" | cut -d' ' -f1)"
  rm -f -- "$PREF_TARGET"
fi

if ((${#TARGET_PACKAGES[@]})); then
APT_CONFIG="$RBAPT/apt-bootstrap.conf" apt-get "${RB_OPTS[@]}" \
  --simulate purge "${TARGET_PACKAGES[@]}" \
  | tee "$EVIDENCE/purge.simulate.txt"
test "$(grep -Ec '^(Remv|Purg) ' "$EVIDENCE/purge.simulate.txt")" \
  -eq "${#TARGET_PACKAGES[@]}"
if grep -Eq '^(Inst|Conf) ' "$EVIDENCE/purge.simulate.txt"; then
  echo 'Unexpected install/configure in purge simulation; abort.' >&2; exit 1
fi
awk '/^(Remv|Purg) / {sub(/:.*/, "", $2); print $2}' "$EVIDENCE/purge.simulate.txt" \
  | sort >"$EVIDENCE/simulated-removals.txt"
diff -u "$EVIDENCE/rollback-package-names.txt" "$EVIDENCE/simulated-removals.txt"
APT_CONFIG="$RBAPT/apt-bootstrap.conf" apt-get "${RB_OPTS[@]}" \
  purge "${TARGET_PACKAGES[@]}" |& tee "$EVIDENCE/purge.txt"
fi
test ! -e /var/lib/dkms/nvidia/610.57.04
test ! -e /usr/src/nvidia-610.57.04

ROLLBACK_DKMS="$BUNDLE/packages/rollback/dkms_2.8.7-2ubuntu2_all.deb"
cp --reflink=never "$ROLLBACK_DKMS" "$RBAPT/cache/archives/"
APT_CONFIG="$RBAPT/apt-bootstrap.conf" apt-get "${RB_OPTS[@]}" \
  --simulate --no-download --no-remove \
  --allow-downgrades install "$ROLLBACK_DKMS" \
  | tee "$EVIDENCE/dkms-rollback.simulate.txt"
python3 - "$EVIDENCE/dkms-rollback.simulate.txt" <<'PY'
import re, sys
for line in open(sys.argv[1], encoding='utf-8'):
    if line.startswith(('Remv ', 'Purg ')):
        raise SystemExit('Unexpected removal during DKMS downgrade')
    if line.startswith(('Inst ', 'Conf ')) and not re.match(
        r'^(?:Inst|Conf) dkms(?::all)?(?: \[[^]]+\])? \(2\.8\.7-2ubuntu2(?: |\))', line
    ):
        raise SystemExit('Unexpected package/version during DKMS downgrade: ' + line)
PY
APT_CONFIG="$RBAPT/apt-bootstrap.conf" apt-get "${RB_OPTS[@]}" \
  --no-download --no-remove \
  --allow-downgrades install "$ROLLBACK_DKMS"
test "$(dpkg-query -W -f='${Version}' dkms)" = 2.8.7-2ubuntu2

printf '%s  %s\n' \
  c7bb0a0569c5347845479ed4e3e4d885c6ee3b8adf068c3401cdf754d5ba3d3b \
  "$RUNFILE" | sha256sum -c -
CC=/usr/bin/gcc-12 sh "$RUNFILE" --dkms
```

The 535 installer stays interactive so its 32-bit choice is visible. Verify
535.183.06 DKMS and peer-memory modules for kernel 6.5, then manually reboot
once into `systemd.unit=multi-user.target`. Validate the nine GPUs, OFED, CUDA
12.2, TensorRT 10.0.1.6, and `nvidia_peermem` before GDM or cameras. Leave the
broken GDS registration quarantined.

If package rollback or runfile reinstall fails, stop and use the checked
Clonezilla image with the recorded system-disk serial. Never select Data2 as
the restore target.
