# pancake0's displays: two X screens and the projector's saved EDID

How pancake0 drives the operator monitor and the omnifin0 projector, why the X configuration names the
projector's EDID, and how to check, refresh or roll it back. The files themselves and their history are
in [`hosts/pancake0/xorg/`](../hosts/pancake0/xorg/README.md).

## The setup in one paragraph

One RTX A6000 runs two **X screens** on display `:1`. Screen 0 (`:1.0`) is the operator's Dell U2723QE
on output DP-0, with the GNOME desktop. Screen 1 (`:1.1`) is the BenQ X3100i projector on output DP-2,
through an active DP-to-HDMI adapter, at 1920x1080 and 240 Hz, with no window manager. Citrus draws the
stimulus there. The two screens are separate: windows can't move between them, and GNOME never touches
screen 1. `rigs/omnifin0/rig.yml` declares the projector's mode (`display_mode`), and Citrus refuses to
START if the live mode differs.

## Terms

- **EDID** (Extended Display Identification Data): about 256 bytes a display sends over the cable to say
  what it is and which resolutions and refresh rates it accepts. The graphics driver reads it when
  the display connects and only offers modes it lists.
- **Hotplug detection**: the cable's signal that tells the GPU a display is present. A projector in
  standby often reports nothing, so the driver sees no display and no EDID.
- **`ConnectedMonitor`** (NVIDIA X option): tells the driver which outputs to treat as connected,
  whatever hotplug says.
- **`CustomEDID`** (NVIDIA X option): tells the driver to use an EDID from a file instead of reading
  one from the display.

## Why the projector needs a saved EDID

X decides at startup which outputs belong to which screen and which mode each gets. On 2026-11-12
pancake0 booted with the projector off:

1. Screen 1 asked for `DP-2: 1920x1080_240`. With no display and no EDID, the driver had no such mode:
   `NVIDIA(1): No valid modes for "DP-2:1920x1080_240+0+0"; removing.` Screen 1 started empty at 640x480.
2. When the projector was switched on later, GNOME on screen 0 saw a new monitor and added it to the
   desktop as an extension to the right of the Dell. The projector showed the desktop, and Citrus's
   screen had no output.
3. Later, after the projector was switched off and on, the live screen 1 showed DP-2 as connected with
   **no mode set**.

So with the plain configuration, the projector had to be on before every boot or gdm restart.

## What the installed configuration does

`/etc/X11/xorg.conf` (repo copy `hosts/pancake0/xorg/xorg.conf.two-screens-forced-edid-v2`, sha256
`73a58f13...`) is the two-screen layout plus, in **both** `Device` sections:

```
Option "ConnectedMonitor" "DP-0, DP-2"
Option "CustomEDID" "DP-2:/etc/X11/benq-x3100i-dp2.edid.bin"
```

- X always treats DP-0 and DP-2 as connected, so both screens get their modes at startup whether the
  projector is on, off or still warming up.
- For DP-2, X uses the saved EDID instead of asking the projector. The file
  (`benq-x3100i-dp2.edid.bin`, 256 bytes, sha256 `9cb0c241...`) is the projector's own EDID, read
  through the adapter on 2026-11-12. It is byte-identical to the projector's live reports, so it allows
  no timing the projector doesn't advertise.
- When the projector is switched on, it receives the signal X is already sending. Nothing on screen 0
  changes.

**Why both outputs, in both sections.** The driver applies `ConnectedMonitor` to the whole GPU, not to
one X screen, even though each X screen has its own `Device` section. The first version (v1) listed
only DP-2 in screen 1's section. The driver then treated DP-0 as disconnected too, and the operator
monitor went dark (log: `NVIDIA(0): Using ConnectedMonitor string "DFP-2"`, then
`No valid modes for "DP-0:..."`). It was rolled back the same day. Listing both outputs, identically in
both sections, leaves nothing to interpret.

**Trade-offs.**
- X will drive DP-0 and DP-2 even if nothing is plugged in. That's harmless here, but if a monitor is
  ever moved to another connector, this file must change with it.
- If the projector is replaced, or its EDID changes (firmware update, another adapter), the saved EDID
  no longer describes it. Refresh it (below).

## Checking it

```bash
sha256sum /etc/X11/xorg.conf /etc/X11/benq-x3100i-dp2.edid.bin   # 73a58f13... and 9cb0c241...
DISPLAY=:1.0 xrandr | grep -E " connected|\*"     # DP-0 only, 3840x2160 60
DISPLAY=:1.1 xrandr | grep -E " connected|\*"     # DP-2 only, 1920x1080 240.00*
grep -E 'ConnectedMonitor string|NVIDIA\([01]\): (Setting mode|No valid)' ~/.local/share/xorg/Xorg.1.log
```

The log should show `Using ConnectedMonitor string "DFP-0, DFP-2"`, then
`NVIDIA(0): Setting mode "DP-0:..."` and `NVIDIA(1): Setting mode "DP-2:1920x1080_240+0+0"`, with no
`No valid modes` line for screen 0 or 1. (`No valid modes` lines for `NVIDIA(G0)`-`(G7)` are the A16
compute GPUs, which have no monitors; they are expected.)

**Is the saved EDID still the projector's?** With the projector on, compare the live EDID with the file:

```bash
DISPLAY=:1.1 xrandr --verbose | awk '/^DP-2/{d=1} d&&/EDID:/{e=1;next} e&&/^\t\t[0-9a-f]+$/{gsub(/\t/,"");printf "%s",$0;next} e{exit}' \
  | xxd -r -p | cmp - /etc/X11/benq-x3100i-dp2.edid.bin && echo "same EDID"
```

Under the forced configuration `xrandr` may report the saved EDID rather than a fresh read. For an
independent check, compare the `Raw EDID bytes` the driver logs for `BenQ PJ (DFP-2)` in an X log from
a server started without `CustomEDID`.

## Refreshing the EDID (new projector, adapter or firmware)

1. Temporarily install `hosts/pancake0/xorg/xorg.conf.two-screens` (no forced EDID), with the projector
   on, and restart gdm.
2. Capture DP-2's EDID with the `xrandr --verbose` pipeline above into a new `.edid.bin`. Check its
   header (`00 ff ff ff ff ff ff 00`), both 128-byte checksums, and the name (`parse-edid`).
3. Put it in `hosts/pancake0/xorg/`, update the sha256 here and in the X README, and install it to
   `/etc/X11/`.
4. Test the forced configuration with `test_xorg_config.sh` (next section) before installing it.

## Testing an X configuration without risking the desktop

`hosts/pancake0/xorg/test_xorg_config.sh CONFIG [SECONDS]` (root) starts a throwaway X server (`:9` on
VT 8) with CONFIG and records `xrandr` for both screens 5 s in. When SECONDS run out, or on any error,
it stops that server and switches back to the desktop's VT. gdm and `/etc/X11/xorg.conf` are not
touched. The monitors show the test server (black) while it runs. Results go to
`/var/log/xorg-test/<time>/`.

Install a new configuration only after it passes with the projector on, off at start, and switched on
mid-run. Then install with the 10-minute automatic rollback in the X README, so a bad result undoes
itself.

## Rolling back

```bash
sudo cp -p /etc/X11/xorg.conf.before-forced-edid-v2 /etc/X11/xorg.conf   # the plain two-screen file, 77dea3e2...
sudo systemctl restart gdm
```

After a rollback, the projector must be on before boot or a gdm restart again.

## Notes

- A gdm restart ends the desktop session, and with it anything started from a terminal inside the
  session (the `agents` tmux). Check with Orange and Citrus first.
- Two projector menu settings matter for unattended power-on. **BenQ HDMI Media Streaming (QS02)** is
  set to Off (System > Operation Settings; changed 2026-11-12), so the projector no longer stops at a
  "dongle not detected" notice that needs an OK. **Quick Auto Search** is On, so if its input loses
  signal the projector may search other inputs. Both belong in the rig's projector settings, not only
  here.
- `/etc/gdm3/custom.conf` has a stray `i` on its first line (since 2025-05), so gdm ignores the whole
  file, including `WaylandEnable=false`. The session is X11 regardless, but the file should be fixed.
- Who to tell when this changes: SpecSail logs the `xorg.conf` sha256. Changes to rate, vsync or swap
  interval go to Palette first. Citrus checks the live mode against `rig.yml`.
