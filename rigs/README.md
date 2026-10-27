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
`projector_firmware_flip`, with the menu state itself in `projector_settings` (below).

### BenQ X3100i Projector Position

Source: BenQ X3100i user manual v1.00 (2023-09), Installation menu pp. 45-46, RS-232 table
pp. 60-64 ([PDF](https://esupportdownload.benq.com/esupport/PROJECTOR/UserManual/X3100i/X3100i_UM_EN_230919170246.pdf),
sha256 `fa90efe9659d26ae8da8bca0cc68bf0e8fe70de81a3057a8f85b563e5579563f`; a local copy is in
`~/Documents/vendor_manuals/benq_x3100i/` on pancake0, not in this repo: vendor copyright).

| Menu (Installation > Projector Position) | RS-232 name (`*pp=` code) | `position_menu` / `position_effective` | `projector_firmware_flip` |
|---|---|---|---|
| Front | Front Table (`FT`) | `front` | `none` |
| Rear | Rear Table (`RE`) | `rear` | `horizontal` |
| Front Ceiling | Front Ceiling (`FC`) | `front_ceiling` | `both` |
| Rear Ceiling | Rear Ceiling (`RC`) | `rear_ceiling` | `vertical` |
| Auto | (no write command) | `auto` / the mode it chose | from the effective mode |

- **Auto.** The manual says the projector picks a position by itself at power-on. With Auto in
  the menu, the menu does not say which flip is applied; `position_effective` must come from
  RS-232 `*pp=?#` or a test pattern. On omnifin0 the body is upside down and points up, so do
  not guess what Auto picks.
- **The flip column is BenQ convention, not stated in the manual.** The manual says where each
  position is used, never which mirroring it applies. Confirm by projecting an asymmetric
  pattern (an "F") and photographing it from above.
- `rigconfig.validate` refuses a `projector_firmware_flip` that disagrees with a declared
  `position_effective`, and a `position_effective` that differs from a non-Auto `position_menu`.

### Projector settings

`projection_paths.<id>.projector_settings` records menu state that changes where or how the canvas
lands. Every key is required (`unknown` allowed) and is part of the physical digest, so changing
one needs a revision bump. Vocabulary is the BenQ X3100i menu; another projector model would add
its own values.

| Key | Values | Menu | RS-232 read |
|---|---|---|---|
| `position_menu` | auto, front, front_ceiling, rear, rear_ceiling | Installation > Projector Position | — (reads the effective one) |
| `position_effective` | front, front_ceiling, rear, rear_ceiling | — | `*pp=?#` |
| `keystone_h`, `keystone_v` | −30 to 30 (want 0: non-zero warps the canvas) | Installation > 2D Keystone | `*hkeystone=?#`, `*vkeystone=?#` |
| `digital_zoom_percent` | 80 to 100 (100 = off) | Game Settings > Digital Zoom | none |
| `aspect_ratio` | auto, 4:3, 16:9 | Display > Aspect Ratio | `*asp=?#` |
| `upscaling_4k` | on, off | Display > 4K Upscaling | none |

The projector's serial number and firmware are instance facts and go in the host binding
(`projector.serial_number`, `projector.firmware_version`): menu Information > Service Code and
Firmware Version, or RS-232 `*serialnumber=?#` and `*sysfwversion=?#` (also `*scalerfwversion=?#`,
`*mcufwversion=?#`, `*modelname=?#`).

**RS-232 readback (not set up).** The X3100i has a 9-pin RS-232 port (pin 2 RX, 3 TX, 5 GND;
baud rate 9600-115200, set in Installation > Baud Rate). Commands are ASCII `<CR>*cmd=?#<CR>`.
pancake0 has no serial adapter today. With a USB-RS-232 cable, a read-only script could capture
the keys above (except digital zoom and 4K upscaling) at install time or per session, instead of
someone reading the menu. The manual does not show the reply format; check it on the device first.

The product relates `citrus.display_canvas_px`, as scanned out with an **unreflected xrandr
output**, to the canvas as it lands on the dish seen from above. It does **not** include:

- **Citrus's OpenGL presentation reflection.** It reaches the scanned-out raster (Citrus
  verified 2026-10-12: the fullscreen quad maps texcoord v=0 to the window bottom and the
  canvas is uploaded row 0 first; the H5 presentation mapping declares the framebuffer as
  the top-left view of the raster delivered to the output, y' = H − y). It is a Citrus
  software fact, so Citrus composes it per session; it does not belong in the rig document.
- **The session's xrandr rotation/reflection/transform.** Citrus captures it per session
  (§4 of the contract). Session canvas parity = `canvas_space_parity` × xrandr reflection
  parity (`X` or `Y` = −1, both = +1, rotations +1).

So a consumer checking a camera.recorded_px → citrus.display_canvas_px homography requires

```
sign(det H) = recorded_space_parity × canvas_space_parity × (−1, GL presentation flip) × xrandr reflection parity
```

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

## Media stack

`media_stack` (optional) lists the layers light crosses on its way from the projector to the fish.
The eye-to-film distance comes from it, and that distance sets stimulus visual angles. Stack facts
describe the assembled rig, so they live here, not in a hardware_library part record.

```yaml
media_stack:
  air_between_film_and_fish: false      # true / false / unknown; false is checked against the layers
  layers:                               # projector side first; the last layer is the fish's water
    - {id: shelf, medium: acrylic, thickness_mm: unknown, refractive_index: unknown}
    - {id: water_under_film, medium: water, thickness_mm: unknown, refractive_index: 1.333,
       refractive_index_source: "..."}  # free text, not in the digest
    - {id: film, medium: film, part_id: optic.fish_imaging.arena_diffuser_disc, thickness_mm: unknown,
       refractive_index: unknown}
    - ...
    - {id: dish_water, medium: water, thickness_mm: unknown, refractive_index: 1.333}   # nominal depth
  arenas:                               # arena id -> {layer id -> overrides}; {} uses the shared stack
    arena_3: {dish_base: {thickness_mm: unknown}}
  assumptions:
    fish_eye_height_above_dish_floor_mm: unknown   # assumed, not built
  evidence: {note, declared_by, declared_at}       # layers may carry their own evidence too
  derived:                              # per arena, computed by rigconfig.py
    arena_3: {eye_to_film_mm: unknown, eye_to_film_paraxial_mm: unknown}
```

- `medium` is one of `air`, `water`, `acrylic`, `polystyrene`, `glass`, `film`, `other`, `unknown`.
  The stack has exactly one `film` layer (the projected surface), and it is not the last layer.
- `air_between_film_and_fish: false` means no layer after the film is `air`. Air before the film
  (projector side) is allowed; it can be omitted, since it changes neither where the image forms
  nor the eye-to-film distance.
- Arena overrides take `part_id`, `thickness_mm` and `refractive_index`, for things that differ
  per arena, such as the dish base.
- The last layer's `thickness_mm` is the nominal water depth. The per-recording fill volume goes
  in session metadata. An assumed eye height above that water's floor must not exceed the depth.
- `eye_to_film_mm` = the thicknesses of the layers strictly between the film and the fish's water,
  plus the assumed eye height. It is measured from the film's fish-side face, so the film's own
  thickness is not counted.
- `eye_to_film_paraxial_mm` = n_eye × Σ tᵢ/nᵢ over the same layers, with the eye height counted
  in the eye's own medium. For flat parallel layers and small angles, a film feature at lateral
  offset x subtends x / that distance at the eye. A product with any `unknown` is `unknown`, as
  with parities. The values are stored, and tests check them against `rigconfig.py`.

Layers, arena overrides, assumptions and the no-air flag are physical, so changing them needs a
revision bump. The assumed eye height counts too, because it feeds a derived value.
`evidence`, `refractive_index_source` and `derived` do not.

## Revisions

`rig_geometry_revision` is bumped whenever a physical field changes, including
`unknown` → declared, since a recording pins what was declared. The physical fields are
everything in `projection_paths.*` and `cameras.*` except `evidence`, `mount_notes` and
`derived`, plus `rig_id` and, when present, `media_stack` (see Media stack). That includes the optional `nominal_geometry`, so editing a
nominal height bumps the revision even though nothing consumes it yet. Free text
(description, provenance, evidence, notes) does not bump.

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
   rig_geometry_revision: <n>
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
