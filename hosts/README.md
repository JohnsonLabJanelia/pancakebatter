# hosts/

One directory per machine, named by `hostname -s`:

```
hosts/<hostname>/
  config.yml                    # system_config v1 inventory (see docs/system_config_v1.md)
  config.captured.yml           # draft from capture_inventory.py; review, then rename to config.yml
  nvidia_driver_upgrade.json    # optional, only for machines with a planned driver upgrade
```

Scripts resolve these through `hostconfig.py` (Python) and `lib/host_config.sh` (bash).
Set `PANCAKEBATTER_HOST=<name>` to work on another machine's files.

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
