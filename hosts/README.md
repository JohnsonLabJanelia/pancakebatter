# hosts/

One directory per machine, named by `hostname -s`:

```
hosts/<hostname>/
  config.yml                    # system_config v1 inventory (see docs/system_config_v1.md)
  config.captured.yml           # draft from capture_inventory.py; review, then rename to config.yml
  nvidia_driver_upgrade.json    # optional, only for machines with a planned driver upgrade
```

Scripts resolve these through `hostconfig.py` (Python) and `lib/host_config.sh` (bash).
Set `PANCAKEBATTER_HOST=<name>` to work on another machine's files, or
`PANCAKEBATTER_HOST_CONFIG=<file>` to point every tool at one explicit file.

Other programs do not read the checkout. `sudo ./install_host_config.sh` publishes
`hosts/<host>/config.yml` to `/etc/pancakebatter/host.yml`; consumers resolve
`$PANCAKEBATTER_HOST_CONFIG` > that path (`python3 hostconfig.py` prints it). Re-run the
installer after editing; `rig_health_check.py` warns while the installed copy is stale.
See `docs/host_config_interface.md` for the keys that are promised.

## Adding a machine

```bash
./capture_inventory.py            # on the new machine; writes hosts/<hostname>/config.captured.yml
./gui_config_editor.py            # fill in roles, IPs, cameras, lenses, and anything not captured
```

`./suggest_nic_layout.py` then applies the pancake0 naming/IP convention to the NIC cards you name
(see `docs/system_config_v1.md`). Capture fills hardware and link facts (system_info, storage_devices, gpus, nics) and leaves NIC
roles, IPs, `cameras`, `pdus` and `kernel_tuning` for you. Needs PyYAML; uses `jsonschema`
if present, else the checks in `check_network_settings.py`.

## Transceiver / NIC serial capture without sudo (read-only helper)

Transceiver EEPROM (`ethtool -m`) and NIC part/serial numbers (PCI VPD) are root-only.
Instead of running the whole capture as root, install a tiny read-only probe once per machine:

```bash
sudo ./install_root_probe.sh            # allows $SUDO_USER; or --user NAME
sudo ./install_root_probe.sh --uninstall
```

This copies `libexec/pancakebatter_root_probe.py` to `/usr/local/libexec/pancakebatter/` (root-owned)
and adds `/etc/sudoers.d/pancakebatter-probe` allowing that exact path, **with no arguments**, for that
user. The probe only reads: it runs `/usr/sbin/ethtool -m <iface>` for physical wired NICs, reads
their sysfs `vpd`, and runs `/usr/sbin/dmidecode -t slot` once (firmware PCIe slot names, recorded
as `system_info.pcie_slots` and each NIC's `pcie_slot`), then prints JSON. `capture_inventory.py` calls it with `sudo -n` (never prompts) and
falls back to normal behavior if it isn't installed. `tests/test_root_probe.py` fails if the probe
ever gains writes, shell use, extra commands, or argument handling.

The installed copy does not update itself: re-run the installer after changing the probe
(needed once now to pick up the slot table).

## Refreshing hardware facts (new transceiver, firmware update, swapped disk)

After `config.yml` has been edited by hand, re-capturing would clobber your choices. Use refresh instead:

```bash
./capture_inventory.py --refresh            # preview what would change in hosts/<hostname>/config.yml
./capture_inventory.py --refresh --write    # apply (validated; timestamped .bak kept)
./capture_inventory.py --refresh --only system_info --write   # just the software versions, e.g. after an upgrade
```

It updates only facts: NIC identity/serials/firmware, transceiver modules, storage, GPUs and
`system_info`. **Caveat (2026-10-08):** `--write` re-serialises the YAML and drops every comment in the file (the `.bak` keeps them); until the save preserves comments, prefer `--only <section>` to review the changes and apply them by hand to a commented config, or re-add the comments after writing. Roles, IPs, MTU, altnames, `link_settings`, cameras, PDUs and kernel tuning are never
touched. It never erases: an empty reading (e.g. no root probe) does not replace a recorded value, and
anything no longer detected is only reported. NICs are matched by MAC address, so renamed ports are
fine; new NICs are reported, not added. It refuses to run against another machine's config.
