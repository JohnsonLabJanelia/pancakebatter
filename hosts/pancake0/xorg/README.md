# pancake0 X configuration

A record of pancake0's X display setup, so the layout in use and its rollback can be recovered.
Nothing in this repo installs these files. `/etc/X11/xorg.conf` is changed by hand (root),
followed by `sudo systemctl restart gdm`, which ends the desktop session and so needs Orange's
clear. Each file here is byte-identical to its source when it was recorded; check with
`sha256sum` before restoring one.

| File here | Source | sha256 | State |
|---|---|---|---|
| `xorg.conf.two-screens` | `/etc/X11/xorg.conf` | `77dea3e2d68ea4110a8563b2d578ff300c92692d0391c41712a8a0d6f0daea99` | **Installed** 2026-11-10 19:09:51 local, X restarted 19:13:39. In use and working. |
| `xorg.conf.single-screen-draft` | compositor-vsync-investigator's `xorg-drafts/xorg.conf.draft-single-screen` | `d34f47ae58579e8bd629cd3b931ba697df5372851f42c2866929b3c77d25fe6b` | Rollback candidate, **never installed or tested**. |
| `xorg.conf.two-screens-forced-edid` | drafted here 2026-11-12 | `89f83479c281547f4cdef3d0128c5f98b68673b585ea8f981a5eee8296ea9cc0` | **Broken, do not install.** Installed 2026-11-12 14:51, gdm restarted 14:53:36: the operator monitor went dark (below). Rolled back to `xorg.conf.two-screens`. Kept as the record of what failed. |
| `benq-x3100i-dp2.edid.bin` | `xrandr --verbose` on `:1.1`, DP-2, 2026-11-12 (projector on, through the active adapter) | `9cb0c241bb3b777b6ac3bce0e5f859a91efa2973b3c7c4fc3193602e77a39bf5` | The projector's 256-byte EDID ("BenQ PJ", BNQ 0x6d04; both checksums valid), for `CustomEDID`. Installs to `/etc/X11/benq-x3100i-dp2.edid.bin`. |
| `monitors.xml` | `~/.config/monitors.xml` (GNOME, per user) | `483cb6605490c01f716f0905b23fcbe7f8a2adc949e5365c4c9ed22d674844a9` | As of 2026-11-10 10:36:45 local. |

## Two screens (installed)

One RTX A6000 drives two X screens on display `:1`:

- **Screen 0**: DP-0, the operator's Dell U2723QE at 3840x2160, managed by GNOME.
- **Screen 1**: DP-2, the BenQ X3100i projector, through the active DP→HDMI adapter, at
  1920x1080 at 240 Hz (`metamodes "DP-2: 1920x1080_240 +0+0"`). No window manager runs here.
  Citrus opens its stimulus window on `DISPLAY=:1.1`.

`hosts/pancake0/config.yml` declares this as `x_screen: 1` on the DP-2 output.
Citrus PR #106 (main 04630ef) and later need both the two-screen layout and that field.

Checked 2026-11-10 against `~/.local/share/xorg/Xorg.1.log`:

- X set the mode on screen 1 at startup: `NVIDIA(1): Setting mode "DP-2:1920x1080_240+0+0"`.
- `xrandr -d :1 --screen 1 --verbose` reads 1920x1080 at 594.000 MHz, 2200 x 1125 total,
  240.00 Hz.

The file's first comment line still says "DRAFT ... (not installed, untested)". It's kept
byte-identical to the installed file, so the header is stale.

The file's comment and the unused `1920x1080_120.00` Modeline are left over from earlier
drafts. They have no effect, because the metamode names the EDID mode `1920x1080_240`.

## Power-on order (2026-11-12) and the forced-EDID draft

With the installed `xorg.conf.two-screens`, **the projector must be on before X starts** (boot or
`sudo systemctl restart gdm`). On 2026-11-12 pancake0 booted with it off:

- `Xorg.1.log`: `NVIDIA(1): No valid modes for "DP-2:1920x1080_240+0+0"; removing.` Screen 1 started
  empty at 640x480.
- When the projector was switched on later, GNOME found DP-2 and added it to screen 0 as an extended
  desktop at 1920x1080 240 Hz (+3840+0). Citrus, on `:1.1`, would have had no projector.
- Restarting gdm with the projector on put DP-2 back on screen 1 (`Setting mode "DP-2:1920x1080_240+0+0"`).

`xorg.conf.two-screens-forced-edid` adds two options to `Device1` (screen 1):

```
Option "ConnectedMonitor" "DP-2"
Option "CustomEDID" "DP-2:/etc/X11/benq-x3100i-dp2.edid.bin"
```

so screen 1 treats DP-2 as connected with the projector's own EDID whether or not the projector answers.
`DP-2` is one of the display's name aliases in the X log (`BenQ PJ (DFP-2)`). The EDID is the projector's
own, read through the adapter, so no timing beyond what it advertises is allowed. Unproven until tested:
that the link comes up when the projector is switched on after X started, still at 240 Hz.

Install (root; check with Orange first, the gdm restart ends the desktop session):

```bash
cd ~/pancakebatter/hosts/pancake0/xorg
sha256sum xorg.conf.two-screens-forced-edid benq-x3100i-dp2.edid.bin   # compare with the table above
sudo install -o root -g root -m 0644 benq-x3100i-dp2.edid.bin /etc/X11/benq-x3100i-dp2.edid.bin
sudo cp -p /etc/X11/xorg.conf /etc/X11/xorg.conf.before-forced-edid-2026-11-12
sudo install -o root -g root -m 0644 xorg.conf.two-screens-forced-edid /etc/X11/xorg.conf
sudo systemctl restart gdm
```

Test, in order:

1. Projector on, after the restart above: `DISPLAY=:1.1 xrandr` shows DP-2 1920x1080 at 240.00, and
   screen 0 shows only DP-0. The X log says `Setting mode "DP-2:1920x1080_240+0+0"` for `NVIDIA(1)`.
2. Projector off (`*pow=off#`, then wait for its cool-down), `sudo systemctl restart gdm`: screen 1 still
   lists DP-2 at 240 and no `No valid modes` line appears.
3. Projector on again (`*pow=on#`): the picture appears on the projector, the live mode is still 240 Hz,
   and screen 0 still has only DP-0. Citrus's own display-mode check passes.

Rollback: `sudo cp -p /etc/X11/xorg.conf.before-forced-edid-2026-11-12 /etc/X11/xorg.conf` (the
installed two-screen file, `77dea3e2…`), then `sudo systemctl restart gdm`. The EDID file can stay.

### What happened when it was installed (2026-11-12)

`ConnectedMonitor` is not per X screen. Both Device sections name the same GPU (`PCI:1:0:0`), and the
driver applied Device1's option to screen 0 too. The greeter's X server (`journalctl -b _PID=<Xorg pid>`)
logged:

- `NVIDIA(0): ConnectedMonitor string: "DP-2"` and then `Using ConnectedMonitor string "DFP-2"`;
- `DFP-0: disconnected`, `BenQ PJ (DFP-2): connected`;
- `NVIDIA(0): No valid modes for "DP-0:nvidia-auto-select+0+0"; removing.` and a 640x480 screen 0.

Screen 1 came up correctly at 1920x1080 on DP-2. Screen 0, where the login screen and the GNOME desktop
run, had no output, so the Dell showed nothing. gdm itself was running. Found by the rescue-gdm and
citrus-agent sessions and pancakebatter from the same log lines. Fixed by the rollback above.

The draft was never tested before it was installed. A retry would list both outputs,
`Option "ConnectedMonitor" "DP-0, DP-2"` in both Device sections, keeping `CustomEDID` for DP-2. That's
untested too: it needs a way back that doesn't depend on the desktop, such as a text console or ssh
session open before the gdm restart.

## Rolling back to one screen

An older Citrus binary, from before #106, opens its window on the default screen. To use one,
pancake0 needs a single-screen `xorg.conf`, then `sudo systemctl restart gdm`.

- **Use `xorg.conf.single-screen-draft` only after testing it.** It puts DP-0 and DP-2 on one
  screen (`metamodes "DP-0: nvidia-auto-select +0+0, DP-2: 1920x1080_240 +3840+0"`). It has
  never been run.
- **Do not restore `/etc/X11/xorg.conf.before-two-screens-2026-11-10`** (sha256
  `6aac753aafeba29b4bcf92f9f590bb753d47e25a0515f0615ac821620e39d87f`). It is the file that was
  in place before the change, but it is stale. Its projector line names DP-3
  `1920x1080_120.00 +3840+0`, from the passive DP++ link replaced on 2026-11-10, and that
  metamode never validated.
- The single-screen 240 Hz mode that worked earlier that day came from GNOME. It was set at
  runtime and persisted in `monitors.xml` (DP-2 1920x1080, rate 240), not from any
  `xorg.conf`.
- `monitors.xml` is per user and keyed by connector and EDID. Its other configuration
  (DP-3, 119.88 Hz) is the layout from before the link swap.
- After a rollback, remove `x_screen` from the host file and reinstall it.
- Check that the live mode is 1920x1080 at 240 Hz, as `rigs/omnifin0/rig.yml` `display_mode`
  declares. Citrus refuses START otherwise.

## Older files in /etc/X11 (not copied)

These are not rollback candidates:

- `xorg.conf.backup` (2025-08-14, `2e162b30…`)
- `xorg.conf.working_2025-05-15_21:24:05` (`cf7c9c0f…`)
- `xorg.conf.nvidia-xconfig-original` (empty)
