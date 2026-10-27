# Equipment events

Hardware moves. A current-state field such as `current_location` loses where the hardware was
before, and git dates only say when someone edited the file, not when the hardware moved. So each
piece of equipment can carry an **append-only list of dated events**. Its location history is
computed from that list.

| File | List | Keyed by |
|---|---|---|
| `equipment/shared.yml` | `items[].events` | item `id` |
| `hosts/<host>/events.yml` (`schemas/host_events.v1.schema.json`) | `components[].events` | `serial_number` (quoted) |

Use the shared file for equipment that moves between hosts and rigs. Use the host file for parts of one
host (drives, cameras, lenses, NICs). The host config stays the current inventory.

## An event

```yaml
- event_id: e2                    # e1, e2, ... unique in the list, never reused
  kind: removed                   # installed | removed | moved | attached | detached | correction
  at: "2026-10-24"                # when the hardware changed: quoted date or ISO datetime
  at_precision: exact             # exact | on_or_before | on_or_after
  from: pancake0                  # optional; checked against where the events put it before
  to: "owner:Frank Loesche (Janelia MCN NET)"
  rig: omnifin0                   # optional, when the host serves more than one rig
  position: "M.2 slot ..."        # optional free text: slot, mount
  by: Jeremy
  reason: returned to its owner while waiting for a hardware quote
  evidence: Jeremy, 2026-10-24
  recorded_at: "2026-10-24"       # when this was written down
```

Locations take one of these forms:
- a host id;
- a rig id;
- `storage` or `storage:<place>`;
- `owner:<person or lab>`, for hardware returned to whoever owns it, such as a loaner;
- `unknown`.

`installed`, `removed` and `moved` follow the same rule: each needs `to`. The different names are
only there for people reading the list. `attached` and `detached` name the other item with
`with: <item id>`, for example a fiber on a spectrometer. Calibration history is not an event in v1:
the `calibration` block holds the current certificate.

## Rules

`equipment_events.py`, `instruments.py` and the tests enforce these:

- **Never edit or delete an event.** Fix a mistake by appending a correction:
  `{kind: correction, corrects: e2, void: true, reason, recorded_at}`, or `corrects` plus
  replacement fields (`at`, `at_precision`, `to`, `from`, `rig`, `with`, `position`). A correction
  needs a reason or evidence. To change an event's kind, void the event and append the right one.
- **Append at the end.** `recorded_at` never decreases down the list.
  `tests/test_equipment_events.py` checks that the events on `origin/main` are still the first
  events of each list, byte for byte. A stored digest could be edited along with the event, so the
  check compares against git instead.
- `at` is not after `recorded_at`. Each `from` matches where the events put the item just before.
  A `detached` needs a matching `attached`.
- **Derived, not stored.** The current location is the `to` of the latest location event in
  physical time (`at`). Ties keep list order, so a backfilled earlier event sorts into place.
  Presence intervals are computed the same way. The one stored copy is a shared item's
  `current_location`, which must equal the derived value. For host components, a part the events
  put elsewhere but `config.yml` still lists, or the reverse, is a warning.
- **Optional in v1.** `events` is an additive key. An item without events keeps a hand-maintained
  `current_location`.

Run records (SpecSail bundles, Citrus captures) stay the ground truth for which serial was used in
which run. The events record where the hardware was.

```bash
python3 equipment_events.py                       # validate shared.yml events and every hosts/*/events.yml
python3 equipment_events.py --history fiber-p50-vis-nir-1
python3 equipment_events.py --history 48804572201297
```

## Rig events

`rigs/<rig_id>/events.yml` (`schemas/rig_events.v1.schema.json`, checked by `rig_events.py`) holds
dated changes to a rig that are not about where hardware is. rig.yml keeps current values; this file
says when they changed, and which part conditions recordings must carry. Same rules as above: never
edit or delete, append at the end, fix with a correction (`void: true` or replacement fields; ids such
as `condition_id`, `item` and `setting` are not correctable, so void and re-append).

```yaml
schema: {name: rig_events, version: 1}
rig: omnifin0
events:
  - event_id: e1
    kind: setting_changed            # setting_changed | condition_observed | condition_cleared | correction
    item: projector.benq.x3100i
    setting: game_and_pic.brightness
    old: 11
    new: 50
    at_precision: window             # exact time unknown: no `at`, two bounds instead
    not_before: "2026-10-24T20:17:50Z"   # old value valid up to here
    not_after: "2026-10-24T22:04:44Z"    # new value valid from here
    recorded_at: "2026-10-27"
    evidence_files:                  # path + sha256; the hash is checked whenever the file is reachable
      - {path: ~/SpecSail/runs/.../settings_change.json, sha256: 6747f0b3...}
  - event_id: e2
    kind: condition_observed         # opens condition_id; condition_cleared closes it
    condition_id: c1
    layer: shelf                     # a rig.yml media_stack layer id
    arenas: [arena_2, arena_3]       # rig.yml media_stack.arenas ids
    condition: scratches
    extent: "..."
    at: "2026-10-27"
    at_precision: on_or_before
    recorded_at: "2026-10-27"
```

- `at_precision: window` is allowed on rig events only. A recording that starts inside a window has
  that setting or condition unknown.
- **Active conditions** are derived: condition_observed minus condition_cleared, in physical time.
  `python3 rig_events.py --active omnifin0 arena_2` lists them.
- **Recordings pin this file** (sha256, plus the commit) next to rig.yml, and copy the active
  conditions for their arenas into the start snapshot. Appending an event does not change rig.yml,
  so a rig.yml pin alone would miss it. `install_host_config.sh` installs it as
  `/etc/pancakebatter/rigs/<rig_id>.events.yml`, with `events_sha256` in the rig sidecar.
- Replacing a part is a physical change: bump rig.yml's revision and append `condition_cleared`.
- **`attested`** records that someone checked a declared rig.yml section on the rig (today only
  `room_orientation`, e.g. with a projected crosshair seen from the door). It pins
  `rigconfig.orientation_digest` (how the projector and cameras are mounted and set, plus
  `room_orientation`; `python3 rigconfig.py rigs/<rig>/rig.yml` prints it) and `rig_geometry_revision`.
  It is current only while rig.yml's digest still equals it: a remount that changes those fields
  ends it, and `rig_events.py` warns until a new check is appended. Picture settings and the media
  stack do not end it.
