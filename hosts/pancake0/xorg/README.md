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
