# Recording-transfer sealer installs

pancakebatter installs released versions of the recording-transfer sealer
(`citrus-recording-transfer`) system-wide, one directory per version, so that
Orange's pre-check and the `/groups` delivery tooling run a pinned, verified
release instead of files in a Citrus checkout. Agreed with the Citrus, Orange
and Palette sessions and approved on 2026-10-10 as part of the move of the
sealer into its own repository (first new release: 3.1.0; 3.0.0 installed as
the rollback).

## Layout

```
/opt/recording-transfer/            paths.recording_transfer_root in the host config
  3.0.0/
    bin/citrus-recording-transfer
    bin/citrus-transfer-completion-marker
    lib/python3.10/site-packages/... the wheel, unpacked
    INSTALLED.json                  version, wheel sha256, source repo/tag/commit, install time
```

Each version is a pip-less venv on `/usr/bin/python3` with the wheel unpacked
into it. Root-owned; directories 0755, files 0644, scripts 0755, so the domain
user that delivers to `/groups` can run them but nobody can change them. There
is no `current` link: a consumer names the exact version directory and pins the
wheel sha256 it expects, checking it against `INSTALLED.json`.

## Trust

`packages/recording_transfer/releases.json` lists every version that may be
installed: wheel file name, sha256, source repository, tag and commit. Adding a
version is a reviewed commit to that file. The installer refuses a wheel that

- is not listed, or whose sha256 differs from its entry;
- contains a file that does not match the wheel's own `RECORD` hash, or a
  `RECORD` entry it does not contain;
- is not pure Python, or whose `METADATA` names another version.

Nothing is fetched from the network. The wheel comes from `wheel_dir` in the
manifest (`/home/jeremy/recording-transfer-wheels` today) or `--wheel`.

## Commands

```bash
./install_recording_transfer.sh list                  # manifest versions and which are installed
./install_recording_transfer.sh install 3.0.0         # root (passwordless through the admin helper)
./install_recording_transfer.sh check                 # every installed file vs RECORD and releases.json
./install_recording_transfer.sh uninstall 3.0.0       # root
```

`rig_health_check.py`'s `recording_transfer` check runs the same verification
every five minutes and warns (and mails) if an installed file was changed or a
version no longer matches its manifest entry.

## Cutover to 3.1.0

1. Citrus releases 3.1.0 from the new repository; its wheel and sha256 are added
   to `releases.json` (reviewed commit) and the wheel saved in `wheel_dir`.
2. `./install_recording_transfer.sh install 3.1.0`; 3.0.0 stays installed.
3. Orange re-pins its pre-check to `/opt/recording-transfer/3.1.0/` and the 3.1.0
   sha256, and confirms.
4. Citrus removes its in-tree copy.
5. 3.0.0 stays as the rollback until everyone agrees to `uninstall 3.0.0`.
