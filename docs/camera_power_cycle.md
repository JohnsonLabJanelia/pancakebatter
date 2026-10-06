# Camera power cycle and lens-mount readiness

`reboot_cams.sh` replaces the bare `pdu.py --action reboot` alias for the
camera outlets. It does two things:

1. `pdu.py --action reboot --outlet all --verify` on the camera PDU
   (192.168.20.177, CyberPower PDU41101). This is the real reset: it removes
   power from the camera body, the Emergent EF mount and the Canon lens. The
   SDK's `EVT_ForceReboot` (Orange's `evt_force_reboot` tool) only restarts
   camera firmware and is for a camera that refuses `EVT_CameraOpen`; it does
   not re-initialize the lens.
2. `evt_camera_ready` from the Orange tree (`ORANGE_CAMERA_READY_ROOT`, default the integration worktree): waits for every expected serial to
   answer discovery, opens each camera without streaming (no root needed),
   and reports `LensMountPresent`, `LensPresent`, `LensBusy`, lens name, mount
   firmware, `Iris`/`IrisCurrent`, `Focus`/`FocusCurrent`, and the exposure
   registers (`Exposure`, `Gain`, `AutoGain`, `LUTEnable`, `Offset`). With
   `--apply-lens` (the default here) it also writes the configured focus and
   iris from the camera config folder, gated on `LensBusy` and verified through
   `IrisCurrent`/`FocusCurrent`. Exit code 0 only when all cameras pass.

Serials come from the PDU outlet descriptions in `hosts/<hostname>/config.yml`
(`SN: 2010093` etc.), so a camera swap only needs the outlet table updated.
Reports land in `logs/camera_power_cycle/camera_ready_<stamp>.json`.

On a terminal the script's own `[reboot_cams]` lines are coloured (cyan progress, green success,
red failure); piped or logged output stays plain, `NO_COLOR` is honoured, and
`REBOOT_CAMS_COLOR=always|never` overrides the detection.

Why the verification matters (measured 2026-09-23 on pancake0): the EF mount
silently drops a Focus or Iris write that arrives while it is still moving,
and the commanded register still reads back the new value. Only
`IrisCurrent`/`FocusCurrent` report the mechanism. A mount that lost its lens
state (counters reset to 0) refuses moves until re-initialized with a write of
0; `evt_camera_ready --init-lens` does that explicitly, and a power cycle does
it implicitly at camera start-up.

Suggested alias:

```bash
alias reboot-cams="~/pancakebatter/reboot_cams.sh"
```
