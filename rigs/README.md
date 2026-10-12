# rigs/

> **Draft** (branch `draft/rig-config-v1`, 2026-10-12) for Palette PR jmdelahanty/palette#377.
> Nothing here is installed yet; the installer section below is the proposal.

One directory per rig, named by `rig_id`:

```
rigs/<rig_id>/
  rig.yml           # rig_config v1: declared physical/optical layout
  revisions.json    # physical digest of every rig_geometry_revision
```

A rig is not a host. One host may drive several rigs and a rig may move hosts, so the
rig document holds only what is physically built, and `hosts/<host>/config.yml` holds a
`rigs:` binding: which rig ids that host drives, which display output drives each
projection path, and the projector hardware on that output.

## Who owns what

| Fact | Owner | Where |
|---|---|---|
| Camera hardware (model, serial, sensor, lens, NIC, IP, transceiver) | pancakebatter | `hosts/<host>/config.yml` `cameras:` |
| Projector hardware, display output → projection path | pancakebatter | `hosts/<host>/config.yml` `rigs:` |
| Projection geometry, camera mount side, mirrors, expected flips/rotation | pancakebatter | `rigs/<rig_id>/rig.yml` |
| Applied camera state per recording (AOI, binning, readbacks) | Orange | start snapshot `camera_geometry_v1` |
| Session display state (xrandr verbose, presentation mapping) | Citrus | session metadata |
| Camera → arena(s), homographies, scale, commissioning | Citrus | registered geometry / calibration |

`serial_number` is the only join between a rig document and host inventory. Camera → arena
is deliberately absent: a camera can observe several sub-arenas or an arena group, and that
is Citrus's per-canvas registered geometry. The camera fields here are **expectations**:
Orange's per-recording readback is checked against them, they are not the applied values.

## Values

Every physical field is a declared value or the string `unknown`. A consumer treats
`unknown` as undeclared, never as a default. Missing and `null` are invalid, so absence
cannot be mistaken for either.

`reverse_x` / `reverse_y` also take `not_supported_by_firmware`: the camera has no
ReverseX/ReverseY node (true of the HB-20000-SB; its GenICam XML has neither), so it cannot
flip. This is distinct from `false` (a supported setting expected off) and from `unknown`.

`camera.nominal_geometry` (`height_above_gel_mm`, `optical_axis_tilt_from_nadir_deg`) is
optional and consumed by no runtime today; it exists for the deferred parallax model. Scale
stays in Citrus calibration.

## Parity rules

Reference view: **the dish seen from above**, image +x right, +y down. Each reflection is a
factor −1; rotations are +1. A product with any `unknown` factor is `unknown`.
`rigconfig.py` is the only implementation; the stored `derived` values must equal it
(tests enforce this), so consumers read them instead of recomputing.

**`projection_paths.<id>.derived.canvas_space_parity`** =
`projection_mode` × `projector_firmware_flip` × (−1)^`optical_mirror_count`

| Field | +1 | −1 |
|---|---|---|
| `projection_mode` (physical, relative to the reference view) | `front`: light reaches the image surface from above | `rear`: light arrives from below, image seen through the surface |
| `projector_firmware_flip` (net menu mirroring) | `none`, `both` (= 180° rotation) | `horizontal`, `vertical` |
| `optical_mirror_count` (projector lens → image surface) | even | odd |
| `projector_mount`, `projector_mount_rotation_deg_about_optical_axis` | always (rotations) | — |

`projection_mode` is the physical geometry, **not** the projector menu. The menu goes in
`projector_firmware_flip`. For a BenQ "Projector Position" setting:

| Menu | `projector_firmware_flip` |
|---|---|
| Front Table | `none` |
| Rear Table | `horizontal` |
| Front Ceiling | `both` |
| Rear Ceiling | `vertical` |

Record the menu wording in the evidence note, and confirm by projecting an asymmetric test
pattern.

The product relates `citrus.display_canvas_px`, as scanned out with an **unreflected xrandr
output**, to the canvas as it lands on the dish seen from above. It does **not** include:

- **Citrus's OpenGL presentation reflection** (`citrus.framebuffer_px` and
  `citrus.stimulus_video_px` are the canvas reflected once vertically). That is a Citrus
  software fact about how those rasters are stored relative to the canvas, not about the
  light path. Citrus composes it on mappings into those spaces and never applies it to
  canvas parity. If Citrus finds that the reflection also reaches the scanned-out image,
  it is still Citrus's to compose per session; it does not belong in the rig document.
- **The session's xrandr rotation/reflection/transform.** Citrus captures it per session
  (§4 of the contract). Session canvas parity = `canvas_space_parity` × xrandr reflection
  parity (`X` or `Y` = −1, both = +1, rotations +1).

**`cameras.<serial>.derived.recorded_space_parity`** =
`viewing_side` × (−1)^`optical_mirror_count` × `reverse_x` × `reverse_y`

| Field | +1 | −1 |
|---|---|---|
| `viewing_side`: side of the dish the optical path leaves toward the camera, before any fold mirror | `dorsal` | `ventral` |
| `optical_mirror_count` (dish → camera) | even | odd |
| `reverse_x`, `reverse_y` | `false`, `not_supported_by_firmware` | `true` |
| `rotation_deg` | always (rotation) | — |

**`cameras.<serial>.derived.expected_camera_to_canvas_handedness.<path>`** = `preserved` when
`recorded_space_parity` × that path's `canvas_space_parity` = +1, `reflected` when −1. It is
keyed by projection path, not arena, and assumes an unreflected xrandr output; Citrus composes
the session's xrandr reflection before comparing with a homography's determinant sign.

## Revisions

`rig_geometry_revision` is bumped whenever a physical field changes, including
`unknown` → declared, since a recording pins what was declared. The physical fields are
everything in `projection_paths.*` and `cameras.*` except `evidence`, `mount_notes` and
`derived`, plus `rig_id`. Free text (description, provenance, evidence, notes) does not bump.

`revisions.json` records the sha256 of the physical fields for each revision
(`rigconfig.physical_digest`). `tests/test_rig_configs.py` fails when rig.yml's physical
fields do not match the entry for its revision, so a physical edit without a bump fails CI.
To change the rig:

```bash
$EDITOR rigs/<rig_id>/rig.yml                       # edit fields, rig_geometry_revision + 1
python3 rigconfig.py rigs/<rig_id>/rig.yml          # copy the printed derived values in; note the digest
$EDITOR rigs/<rig_id>/revisions.json                # add "<rev>": {physical_sha256, declared_at, change}
```

Set `last_physical_change` when the hardware itself moved, not when a declaration was added.

## Install (proposed, not implemented)

`sudo ./install_host_config.sh` would, for each `rigs[].rig_id` in the host config:

1. validate `rigs/<rig_id>/rig.yml` with `rigconfig.validate` and the binding with
   `rigconfig.validate_binding`; refuse on any error;
2. refuse when rig.yml has uncommitted changes (a recording must be able to pin a commit;
   host.yml only flags this, rigs would refuse unless `--allow-dirty`);
3. install it root-owned 0644 to `/etc/pancakebatter/rigs/<rig_id>.yml`
   (under `paths.shared_config_root`);
4. write `/etc/pancakebatter/rigs/<rig_id>.yml.source`:

   ```
   source: rigs/omnifin0/rig.yml
   commit: <full 40-character sha>
   installed: <ISO time>
   sha256: <sha256 of the installed file>
   rig_geometry_revision: 4
   ```

   and switch `host.yml.source` to the full commit sha too;
5. remove installed rig files the host no longer binds.

`rig_health_check.py` would warn while an installed rig file is stale, as it does for host.yml.

Consumers resolve `$PANCAKEBATTER_RIG_CONFIG` (one explicit file) > `/etc/pancakebatter/rigs/<rig_id>.yml`,
with `rig_id` from the host's `rigs:` binding. A recording pins `rig_id`,
`rig_geometry_revision`, the installed file's `sha256` and the sidecar's `commit`.

## Consumers

Programs declare the keys they read in `schemas/consumers/<program>.json` under
`documents.rig_config` (format in `schemas/consumers/README.md`); keys must be on
`schemas/rig_config.v1.stable_keys.json`. Citrus's draft declaration is in
`schemas/consumers/citrus.json`; the README shows how Orange would declare itself.
