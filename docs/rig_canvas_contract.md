# Rig/Canvas Config Contract

Canonical cross-repo contract: `/home/jeremy/agent-contracts/pancake-orange-citrus/rig_canvas_inventory_contract.md`.

This local note summarizes the same boundary from the `pancakebatter` side and should stay aligned with the cross-repo contract above.

## Canonical Owners

| Concern | Canonical location | Stable IDs | Notes |
|---|---|---|---|
| Static machine and camera inventory | `pancakebatter/hosts/<host>/config.yml` | `host_id`, `camera_serial` | Owns hardware facts that change when the host is rewired or rebuilt. For `pancake0`, use `hosts/pancake0/config.yml`. |
| Per-camera runtime capture presets | `~/orange_data/config/local/<preset>/<camera_serial>.json` and `~/orange_data/config/network/...` | `camera_serial` | Owns startup settings for orange. Not a source of truth for hardware inventory or calibration. |
| Rig/canvas/arena calibration and geometry | `citrus/targets/rigs/<rig_id>/<canvas_name>/` | `rig_id`, `canvas_name`, `arena_id`, `camera_serial` | Owns canvas JSON, arena geometry, per-camera calibration, and calibration artifacts. |

## Where consumers read the host config

Programs other than pancakebatter read the *installed* copy, resolved as
`$PANCAKEBATTER_HOST_CONFIG` > `/etc/pancakebatter/host.yml` > error, never a
checkout path under a home directory. `install_host_config.sh` publishes it;
`rig_health_check.py` warns when it is stale. The promised keys and the
consumer declaration are in [`host_config_interface.md`](host_config_interface.md).

## Stable IDs

- `host_id`: use `system_info.hostname` from the host config.
- `camera_serial`: use `cameras[*].serial_number` from the host config.
- `rig_id`: use the citrus rig directory name under `targets/rigs/<rig_id>/`.
- `canvas_name`: use both the citrus canvas directory name and the `canvas_name` field in the canvas JSON.
- `arena_id`: use the key under `arenas` in the citrus canvas JSON.

## Cross-Repo Join Rules

- The YAML map key under `pancakebatter/hosts/<host>/config.yml:cameras` is a local inventory key. It may stay as camera MAC address, but it is not the cross-repo join key.
- The cross-repo join key for a camera is always `camera_serial`.
- `orange` camera config filenames must use `<camera_serial>.json`.
- `citrus` `camera_calibrations[].camera_id` must equal the same `camera_serial`.
- `rig_id`, `canvas_name`, and `arena_id` belong to citrus-owned rig metadata. Other repos may reference those IDs, but should not duplicate citrus calibration payloads.

## Ownership Rules

- Put data in `pancakebatter` if it changes when the host is physically rewired, rebuilt, or the attached hardware changes.
- Put data in `orange` if it changes when an operator selects a capture preset or adjusts runtime camera behavior.
- Put data in `citrus` if it changes when a canvas or arena is calibrated, renamed, resized, or assigned different experimental geometry.

## Current Scope By Repo

`pancakebatter/hosts/<host>/config.yml` is the source of truth for facts like:

- `system_info.hostname`
- camera `serial_number`
- camera `lens`
- camera `sensor`
- camera `nic_port`
- camera `ip_address`
- NIC and transceiver inventory

`orange` per-camera JSON is the source of truth for runtime settings like:

- `name`
- `width`
- `height`
- `frame_rate`
- `gain`
- `exposure`
- `pixel_format`
- `color_temp`
- `gpu_id`
- `gpu_direct`
- `focus_uart_bootstrap`
- `color`
- `focus`
- `iris`

`citrus` rig/canvas JSON and artifacts are the source of truth for:

- `canvas_name`
- `arenas`
- `camera_calibrations`
- `active_camera_id`
- `selected_dish_type_name`
- arena geometry and real-world dimensions
- homography and scale artifacts
- arena-level IPC source and trigger fields

## Optional Deployment Binding In Host Config

If a host has a stable physical affiliation to a rig or canvas, its host config may carry a small deployment block with IDs only. This is allowed because it describes where the machine is deployed, not the calibration itself.

Example:

```yaml
deployment:
  rig_id: omnifin0
  default_canvas_name: casper
  camera_bindings:
    "2010093":
      arena_id: arena_1
    "2010094":
      arena_id: arena_2
```

Rules for this block:

- Keep it limited to identifiers and static physical affiliation.
- Do not copy `camera_calibrations`, homography matrices, scale values, or arena geometry into the host config.
- Only include `camera_bindings` if the camera-to-arena relationship is physically fixed for that machine.
- No code currently reads this block. It is a contract for future integration, not an active runtime interface yet.

## Anti-Patterns

- Do not make `orange` the owner of rig, canvas, or arena metadata.
- Do not copy full camera hardware inventory into `orange` camera JSON.
- Do not put citrus calibration blobs into the host config.
- Do not maintain multiple hand-edited copies of a full host config across repos.
- If `citrus` needs machine inventory, point it at `pancakebatter/hosts/<host>/config.yml` or generate a minimal read-only subset instead of copying the file.

## Minimum Contract Summary

- `pancakebatter` is machine-inventory-centric.
- `orange` is camera-runtime-centric.
- `citrus` is rig/canvas/arena-centric.
- The shared camera identifier is `camera_serial`.
- The shared rig identifiers are `rig_id`, `canvas_name`, and `arena_id`.
