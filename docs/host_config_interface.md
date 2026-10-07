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
./install_host_config.sh --check     # is the installed copy current?
```

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
| cameras (map keyed by MAC; the key itself is local, `serial_number` is the join key) | `serial_number`, `ip_address`, `nic_port`, `interface`, `model`, `lens`, `sensor` |
| nics (list of one-key maps: `nics.*.*.<field>`) | `role`, `mac_address`, `pcie_id`, `ip_address`, `mtu`, `expected_link` |
| pdus | `ip_address`, `outlets.*.description`, `outlets.*.camera_serial` (preferred over parsing `SN:` from the description) |
| kernel_tuning | `isolated_cores`; `core_roles.*.{role, consumer, camera, sibling_of, thread}` |
| paths | `data_root`, `orange_run_dir`, `shared_config_root` |

Everything else is internal and may change without notice: the version
snapshot fields in `system_info`, `storage_devices`, `gpus`, transceiver
details, `kernel_tuning.camera_ports.*.mlx5_irq_cpus` (a recording; the check
uses the invariant), `sysctl`, `transparent_hugepage`, `cmdline`.

Keys listed under `optional_keys` in the stable-keys file (camera `model`, `lens`,
`sensor`, `interface`; outlet `camera_serial`; the descriptive `core_roles` fields;
`paths.orange_run_dir`, `paths.shared_config_root`) are promised in meaning and
type when present, but a host may lack them (a camera recorded before its lens
was, for instance); read them defensively. Every other stable key exists on every
host that has the section.

Rules: additive changes stay in version 1; renaming or removing a stable key
bumps `schema.version` and is announced to every consumer on file.
`pdus`, `kernel_tuning` and `paths` are optional sections (dumpling has none of
them), so a consumer checks for the section before reading into it.

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
