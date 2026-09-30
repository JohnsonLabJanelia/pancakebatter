# Console entry for the pancake0 driver maintenance

Jeremy resumed Codex on tty3 and opened an authenticated root shell on tty4.
The original package bundle remains frozen. This companion implements only
the initial backup and desktop shutdown; the later driver procedure remains
in `nvidia_driver_maintenance.md`.

From the root shell on tty4, after saving desktop work:

```bash
cd /home/jeremy/pancakebatter
/usr/bin/python3 nvidia_driver_enter_console.py --enter
```

The helper verifies the pinned bundle checksum catalog, every bundle file,
the old driver installer, nine GPU identities, kernel/driver/CUDA/TensorRT/OFED,
clean DKMS, no GPU compute jobs, and no active package installer. It accepts
the live `gdm3.service` alias of `gdm.service`. It requires a root Linux VT,
including the pseudo-terminal created by `sudo -i` inside a verified VT login.
For that sudo case, it follows kernel process ancestry back to the root
`login` process on the VT. Desktop and SSH pseudo-terminals remain rejected;
the `SUDO_TTY` environment variable alone is not accepted as proof.

It records the initial service states, stops package automation, and creates
a private tar archive of `/var/lib/dkms`, `/boot`, `/var/lib/nvidia`,
`/etc/dkms`, `/var/lib/dpkg/status`, and `/etc/apt`. It compares the archive
against the source files and records SHA-256 before stopping GDM explicitly.
Stopping GDM directly is sufficient here; it avoids changing unrelated units
through target isolation. The saved boot target stays `graphical.target`.

It checks that the desktop is inactive, no GPU device users remain, and OFED
is still active, then prints `CONSOLE ENTRY COMPLETE`. It leaves the old driver
loaded and installed. It does not uninstall, install, unload modules, change
the boot default, or reboot. Return to Codex with Ctrl+Alt+F3 for the next stage.

Evidence goes to a new `/mnt/Data2/nvenc-driver-console-entry-<timestamp>-<suffix>`
directory. The root-only archive and baseline files use mode 0600. Directory
mode 0711 permits reading the explicitly named `status.json`, which is mode
0644 and contains only progress, the backup hash, and any error. The agent can
read that status after the operator runs the command.

On any error, the helper stops and records it. Services already stopped stay
stopped for review; it does not automatically restart the desktop or kill GPU
applications. `services-before.json` preserves their original states. The
driver remains installed throughout this stage.

For read-only validation before entering maintenance:

```bash
/usr/bin/python3 nvidia_driver_enter_console.py --check
```

Validation before initial publication: live read-only checks passed; five guard tests
passed (normal-user refusal, desktop-PTY refusal, truncated installer process
detection, failed backup comparison blocking desktop shutdown, and new compute
activity blocking desktop shutdown).
The mutating `--enter` mode was not executed by the agent.

The first attempt stopped before preflight or changes because the original
terminal-name check rejected sudo's `/dev/pts/4` despite its tty4 login.
The corrected guard was verified against that live root-shell process chain,
with regression tests for sudo on a VT, desktop/SSH rejection, and missing or
inconsistent ancestry.

This readiness report is a snapshot. The subsequent unload/uninstall stage must
recheck GPU users and package activity immediately before changing the driver.
When continuing the original manual runbook, initialize its `EVIDENCE` variable
to the directory printed by this helper, along with the documented `BUNDLE`,
`RECOVERY`, and `RUNFILE` variables; a child script cannot set its parent's shell
variables.
