# Apt Package Manifests

Each manifest in this directory contains one apt package name per line.

- Blank lines are ignored.
- Lines beginning with `#` are comments.
- Installer scripts load these manifests through [`lib/apt_packages.sh`](../../lib/apt_packages.sh).
- `yq` is managed separately by [`install_yq.sh`](../../install_yq.sh) because the network scripts require `yq e` compatibility.

Keep manifests focused on a machine role or installer so package changes stay reviewable.

Current manifests:

- `base.txt` - core bootstrap packages installed by `apt_installs.sh`.
- `network_tools.txt` - networking utilities installed by `apt_installs.sh`.
- `build_tools.txt` - extra toolchain packages installed by `apt_installs.sh`.
- `sysadmin.txt` - admin, storage, and network diagnostics installed by `apt_installs.sh`.
- `cuda_test.txt`, `enet.txt`, `ffmpeg_cuda.txt`, `opencv.txt` - per-installer build dependencies.
