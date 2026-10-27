# The host configuration as an interface

`hosts/<hostname>/config.yml` in this repo is the record of what a machine *is*:
cameras, NICs, PDUs, GPUs, storage, kernel tuning and core roles, machine-level
paths. Other programs on the machine (Citrus, Orange, ...) should read those
facts from here instead of keeping their own copies. This page is the contract
for doing that. Ownership boundaries between the repos are in
[`rig_canvas_contract.md`](rig_canvas_contract.md); this page covers *where
the file is* and *which keys are promised*.

## Where consumers find it

Resolution order, the same for every program:

1. `$PANCAKEBATTER_HOST_CONFIG`, an explicit file path, if set.
2. `/etc/pancakebatter/host.yml`, the installed copy.
3. Otherwise fail with a message. **Never** fall back to a path under someone's
   home directory; removing those guesses is the point.

Reference implementations: `hostconfig.resolve_host_config()` (Python) and
`resolve_host_config` in `lib/host_config.sh` (bash); `python3 hostconfig.py`
prints the resolved path for any other language.

The installed copy is a root-owned file, not a symlink into a checkout (a
symlink defeats the shared-machine goal: permissions, and it dies with the
user). It is published from the checkout:

```bash
sudo ./install_host_config.sh        # validates, installs /etc/pancakebatter/host.yml + host.yml.source (commit, time, sha256)
./install_host_config.sh --check     # are the installed copies current?
```

The same run installs the rig documents the host's `rigs:` block names, as
`/etc/pancakebatter/rigs/<rig_id>.yml` (+ `<rig_id>.events.yml` when the rig has an events ledger)
with a `.yml.source` sidecar, and removes rig files the host no longer binds; see
[rigs/README.md "Install"](../rigs/README.md#install). Consumers resolve
`$PANCAKEBATTER_RIG_CONFIG` > that path.

To run the installers without typing a password, `sudo ./install_admin_helper.sh` installs a
root-owned helper and a `sudoers.d` rule for it; afterwards `./install_host_config.sh`,
`./install_ptp_units.sh` and `./install_rig_health_timer.sh` re-run themselves through it. The
helper executes the checkout's scripts as root, so it amounts to passwordless root for the
checkout owner: appropriate while that person is the machine's sudoer, to be removed
(`--uninstall`) before the checkout is shared.

The checkout stays the place to edit (`capture_inventory.py`,
`gui_config_editor.py`, `camera_net_config.py`, by hand); re-run the installer
after every change. Two things catch a forgotten re-run: `rig_health_check.py`'s
`host_config` check warns (and mails) when the installed copy differs from the
checkout, and the `.source` sidecar says which commit it came from.

For pancakebatter's own tools the checkout copy remains canonical
(`hostconfig.default_config_path()`, `HOST_CONFIG_FILE`); they honour
`PANCAKEBATTER_HOST_CONFIG` too, so a tool can be pointed at the installed
copy or a test file.

## What is promised (schema version 1)

`schemas/system_config.v1.stable_keys.json` is the list. In words:

| Area | Stable keys |
|---|---|
| identity | `schema.name`, `schema.version`, `system_info.hostname` (= `host_id`) |
| cameras (map keyed by MAC; the key itself is local, `serial_number` is the join key) | `serial_number`, `ip_address`, `nic_port`, `interface`, `model`, `lens`, `sensor`, `sensor.x_resolution`, `sensor.y_resolution` (the rest of `sensor` is inventory), `part_id`, `lens.part_id` |
| nics (list of one-key maps: `nics.*.*.<field>`) | `role`, `mac_address`, `pcie_id`, `ip_address`, `mtu`, `expected_link` |
| pdus | `ip_address`, `outlets.*.description`, `outlets.*.camera_serial` (preferred over parsing `SN:` from the description) |
| kernel_tuning | `isolated_cores`; `core_roles.*.{role, consumer, camera, sibling_of, thread}` |
| paths | `data_root`, `orange_run_dir`, `shared_config_root` |
| light_arrays | `name`, `pdu`, `outlet`, `wavelength_nm`, `strobe.source`; optional `part_id`, `strobe.{camera_serial, gpio_line, daq_channel, via}` |
| daqs | `name`, `channels.*.{id, direction, kind, signal, source.kind}`; optional `shared_id`, `part_id`, `serial_number`, `channels.*.source.{camera_serial, gpio_line, name}`, `calibration` |
| photodetectors | `name`; optional `shared_id`, `part_id`, `serial_number`, `filter`, `daq_channel`, `calibration` |

Everything else is internal and may change without notice: the version
snapshot fields in `system_info`, `storage_devices`, `gpus`, transceiver
details, `kernel_tuning.camera_ports.*.mlx5_irq_cpus` (a recording; the check
uses the invariant), `sysctl`, `transparent_hugepage`, `cmdline`.

Keys listed under `optional_keys` in the stable-keys file (camera `model`, `lens`, `part_id`, `lens.part_id`,
`sensor`, `interface`; outlet `camera_serial`; the descriptive `core_roles` fields;
`paths.orange_run_dir`, `paths.shared_config_root`) are promised in meaning and
type when present, but a host may lack them (a camera recorded before its lens
was, for instance); read them defensively. Every other stable key exists on every
host that has the section.

Rules: additive changes stay in version 1; renaming or removing a stable key
bumps `schema.version` and is announced to every consumer on file.
`pdus`, `kernel_tuning`, `paths`, `daqs`, `photodetectors` and `light_arrays` are
optional sections (dumpling has none of them), so a consumer checks for the
section before reading into it. A stable key may be declared in the schema
before any host has it (`daqs` before the first DAQ is installed);
`tests/test_consumers.py` accepts that and still rejects a key the schema does
not declare.

### Machine paths

`paths` holds machine-level locations: `data_root` (recorded data and runtime
camera presets, today `/home/jeremy/orange_data`), `orange_run_dir`
(`/run/orange`), `shared_config_root` (`/etc/pancakebatter`). Values may embed
a username until the shared-machine migration moves data to shared storage;
recording them here is still the single place to change. Interpreter and
conda locations are deliberately *not* here: they are per-user tooling.
Scripts discover environments (next to `$CONDA_EXE`, then `~/miniforge3` and
friends) or take an explicit variable, as `reboot_cams.sh` does with
`RIG_CONTROL_PYTHON`; the environments themselves are described in
`environments/*.yaml`.

## Declaring yourself as a consumer

Add `schemas/consumers/<program>.json` (format in
`schemas/consumers/README.md`): schema version and the key paths you read.
`tests/test_consumers.py` then fails if any declared key is not on the stable
list or does not resolve in a host config that has the section. That turns the
promise into a test that breaks here before it breaks you.

## Cross-checks instead of copies

Where a consumer makes a choice that must agree with a fact here, check it,
don't copy it:

- Citrus pins its render and arena update threads to CPUs 1 and 2
  (`citrus_runtime.threading`). `kernel_tuning.core_roles` records the same CPUs
  with `consumer: citrus`. Citrus should compare the two at startup and log a
  mismatch; pancakebatter does not read Citrus's YAML (that would reintroduce
  cross-repo path coupling).
- Planned on this side: `rig_health_check.py` verifying live thread pinning
  from `/proc` against `core_roles` (a thread pinned to an isolated CPU with no
  role, or a role CPU with nothing on it while its consumer runs).

## Transition for an existing consumer

1. Read machine facts (cameras, pdus, system_info, paths) from the resolved
   file. Keep your own runtime configuration in your own file.
2. During cutover, fall back to your local copy only with a loud warning that
   names the resolver; then delete the copy.
3. Add your `schemas/consumers/<program>.json`.

### Part ids

`cameras.*.part_id`, `cameras.*.lens.part_id` and `rigs.*.display_outputs.*.projector.part_id` are optional links from an instance
(this serial, on this host) to its part design in `~/hardware_library`
(`parts/<category>/<part_id>/part.yml`: CAD, datum frame, dimensions). The id starts
with its category (`camera.`, `lens.`, `projector.`). `tests/test_host_configs.py` checks the
prefix always and, when a hardware_library checkout exists
(`$PANCAKEBATTER_HARDWARE_LIBRARY`, default `~/hardware_library`), that the part
exists and that the camera's `model` is one of the part's `links.pancakebatter_models`.
The instrument sections below follow the same rule: `daqs.*.part_id` starts `daq.`,
`photodetectors.*.part_id` `photodetector.`, `light_arrays.*.part_id` `light_source.`,
a filter's `part_id` `filter.`; a shared item's id may be any category. The same test
checks them, and `equipment/shared.yml`, against hardware_library.

## Instruments

Three optional sections record bench instruments: what they are, what is wired
where, and whether their calibration still applies. `instruments.py` holds the
cross-checks; `configio.validate()` runs them, so the editor, `capture_inventory.py`
and `install_host_config.sh` refuse a config that fails them.

```yaml
light_arrays:
  - name: light-array-1        # must equal pdus[pdu].outlets[outlet].name
    pdu: pdu1
    outlet: 7
    part_id: light_source.<maker>.<model>   # optional; omitted while unknown
    wavelength_nm: 850
    driver: null               # free text; null while unknown
    strobe:
      source: camera_gpio      # camera_gpio | daq | none | unknown
      camera_serial: "2010096" # camera_gpio: must be in cameras
      gpio_line: null          # camera_gpio: integer, null until confirmed
      daq_channel: {daq: daq0, channel: DIO2}   # daq: must exist
      via: unknown             # direct | driver_board | unknown (camera_gpio and daq)
    note: free text

daqs:
  - name: daq0
    part_id: daq.<maker>.<model>            # optional
    serial_number: "01ABCDEF"               # or shared_id: <equipment/shared.yml id>
    firmware: null
    driver: {name: uldaq, version: "1.2.1"}
    channels:
      - id: AI0                # as the device labels it: AI0, DIO3, CTR0
        direction: in          # in | out
        kind: analog           # analog | digital | counter
        signal: corner_tl
        source: {kind: photodetector, name: pd-tl}
        # {kind: camera_gpio, camera_serial: "2010096", gpio_line: 1}
        # {kind: light_array, name: light-array-1}
        # {kind: host}
        # {kind: other, note: "projector vsync tap"}   (note required)
    calibration: {...}         # optional, see below

photodetectors:
  - name: pd-tl
    part_id: photodetector.thorlabs.pda36a2
    serial_number: "..."                    # or shared_id
    settings: {gain_db: 40}                 # parts with front-panel settings
    filter: {part_id: filter.<maker>.<model>, serial_or_lot: "...", fitted_at: "2026-10-20"}   # or: none
    daq_channel: {daq: daq0, channel: AI0}  # that channel's source must name this photodetector
    calibration: {...}                      # must include filter
```

A calibration block (DAQs, photodetectors, shared items):

```yaml
calibration:
  certificate_id: "C-123"      # null for an in-house calibration without one
  calibrated_at: "2026-10-02"
  due: "2027-10-02"
  performed_by: Thorlabs
  filter: {part_id: filter.<maker>.<model>, serial_or_lot: "..."}   # photodetectors: required; or none
```

Quote dates: YAML loads an unquoted `2026-10-02` as a date object, which the
schema rejects as not a string.

Rules beyond the schema (errors unless noted):

- DAQ channel sources name a camera serial in `cameras`, a photodetector or light
  array by name on this host; `other` needs a `note`. Channel ids are unique per DAQ.
- A photodetector's `daq_channel` names an existing DAQ and channel, and that
  channel's `source` names the photodetector back.
- A light array's `pdu`/`outlet` exists and the outlet's `name` is the array's
  `name`. `strobe.camera_serial` is in `cameras`; `strobe.daq_channel` exists.
- **Filter vs calibration.** A photodetector's response is calibrated through its
  filter, so the fitted `filter` must be the calibration's `filter` (same
  `part_id` and `serial_or_lot`; `none` matches only `none`; `fitted_at` is not
  compared). Swapping, removing or not recording the filter is an error: the
  calibration no longer applies. Recalibrate, or refit the calibrated filter.
- **Overdue.** A `due` date before today is a *warning*: `configio.validate()`
  still passes and `configio.warnings()` returns it; `install_host_config.sh`
  prints it.

Not-yet-known values follow the rest of the file: an optional link such as
`part_id` is omitted (as on cameras without one), a scalar fact known to exist
but not yet read is `null` (as NIC `part_number`), and an enum carries `unknown`
(as NIC `role`). pancake0's light arrays are the example.

## Shared equipment

Equipment that moves between hosts and rigs (a calibration photodetector, a
power meter, a loaner DAQ) is recorded once in `equipment/shared.yml`
(`schemas/shared_equipment.v1.schema.json`), not in each host config:

```yaml
schema: {name: shared_equipment, version: 1}
items:
  - id: pd-cal-1                     # stable handle, never reused
    part_id: photodetector.thorlabs.pda36a2   # optional
    serial_number: "..."
    description: calibration detector for the fish-plane probe
    home_location: storage           # a host id, a rig id, or storage[:<place>]
    current_location: pancake0
    calibration: {...}               # optional, same block as above
```

A DAQ or photodetector on a host uses an item with `shared_id: pd-cal-1` in
place of `part_id`, `serial_number` and `calibration`, which come from the item
(repeating any of them is an error). It keeps its own `name`, wiring and fitted
`filter`, so the filter rule compares the host's fitted filter with the item's
calibration. An unknown `shared_id` is an error; an item whose
`current_location` is not the referencing host is a warning. When an item moves,
append an event to its `events` list and set `current_location` to match; the
events are its dated, never-edited history ([equipment_events.md](equipment_events.md)). Check the file, and optionally a host, with
`python3 instruments.py [--host <hostname>]`.

Open: `install_host_config.sh` publishes only `host.yml`, not the shared file,
so a consumer cannot yet resolve a `shared_id` from the installed copy (and must
not guess a checkout path). Publishing `equipment/shared.yml` next to it is the
likely fix once a host uses a shared item.
