# Apt Package Manifests

Each manifest in this directory contains one apt package name per line.

- Blank lines are ignored.
- Lines beginning with `#` are comments.
- Installer scripts load these manifests through [`lib/apt_packages.sh`](../../lib/apt_packages.sh).
- `gh` in `sysadmin.txt` comes from GitHub's apt repository, which [`install_gh_apt_repo.sh`](../../install_gh_apt_repo.sh) adds (keyring checked against pinned fingerprints); `apt_installs.sh` runs it before the manifests. `./install_gh_apt_repo.sh --check` reports the state without root.
- `yq` is managed separately by [`install_yq.sh`](../../install_yq.sh) because the network scripts require `yq e` compatibility.

Keep manifests focused on a machine role or installer so package changes stay reviewable.

Current manifests:

- `base.txt` - core bootstrap packages installed by `apt_installs.sh`.
- `network_tools.txt` - networking utilities installed by `apt_installs.sh`.
- `build_tools.txt` - extra toolchain packages installed by `apt_installs.sh`.
- `sysadmin.txt` - admin, storage, and network diagnostics installed by `apt_installs.sh`.
- `data_diagnostics.txt` - HDF5 CLI tools, graphviz, imagemagick, and Tk installed by `apt_installs.sh`.
- `python.txt` - the `python3-*` modules the repo's tools import under the system python, installed by `apt_installs.sh`. Install system-python modules from apt, not `sudo pip`; `./capture_inventory.py --refresh --only system_python` records what is actually there (see `docs/system_config_v1.md`).
- `domain_join.txt` - SSSD/Kerberos/realmd packages for joining the machine to a domain. Opt-in: not run by `apt_installs.sh` because `krb5-user` prompts for a realm and joining is a deliberate step. Install with:

  ```bash
  sudo bash -c 'source lib/apt_packages.sh && install_apt_manifest_packages "domain join packages" domain_join'
  ```

- `cuda_test.txt`, `enet.txt`, `ffmpeg_cuda.txt`, `opencv.txt` - per-installer build dependencies.
