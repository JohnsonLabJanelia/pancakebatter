# Camera NIC Network Configuration

This repo now uses NetworkManager for camera NIC configuration. The earlier
netplan/systemd-networkd notes are historical; do not edit `/etc/netplan/*.yaml`
or run `netplan apply` for the camera NICs unless you are intentionally replacing
the current NetworkManager workflow.

The active machine config is named from the short hostname:

```bash
./hosts/$(hostname -s)/config.yml
```

For `pancake0`, that file is `hosts/pancake0/config.yml`. The network scripts derive this
path internally from the hostname, so they work from any directory.

The config uses `system_config` v1. The schema contract is documented in
[`system_config_v1.md`](system_config_v1.md), with a JSON Schema at
[`../schemas/system_config.v1.schema.json`](../schemas/system_config.v1.schema.json).
For the operational checklist used when moving a camera between NIC ports, see
[`camera_nic_move_runbook.md`](camera_nic_move_runbook.md).

## What The Config Owns

The hostname config is the source of truth for:

- Persistent NIC names, aliases, MAC addresses, MTU, static host IPs, and link settings.
- Camera MAC/serial inventory.
- Camera `nic_port` assignments.
- Camera IP addresses.

The important relationship is that every camera IP should live on the subnet of the
host NIC listed in its `nic_port`.

Example:

```yaml
nics:
  - mlnx1_p1_25g:
      altname: eth0
      role: camera
      managed: true
      expected_link: true
      mac_address: a0:88:c2:69:11:9e
      mtu: 9000
      ip_address: 192.168.110.1/24
      link_settings:
        speed: 25000
        autoneg: true

cameras:
  E0-55-97-1E-AB-ED:
    serial_number: 2010093
    ip_address: 192.168.110.2
    nic_port: mlnx1_p1_25g
```

In this example, the host NIC is `192.168.110.1/24` and the attached camera is
`192.168.110.2`.

Use `managed: false` for ports that should stay in hardware inventory but should
not be configured or checked as active camera links. The scripts still create
stable names for unmanaged NICs, but `create_nm_connections.sh`,
`configure_interfaces.sh`, and the network checker skip their operational
NetworkManager/link requirements.

Activate an unmanaged spare before assigning a camera to it:

```bash
./camera_net_config.py activate-nic --nic mlnx2_p4_25g
./camera_net_config.py activate-nic --nic mlnx2_p4_25g --apply
```

## Before Changing The Network

Back up both the host networking files and the repo config:

```bash
sudo ./network_backup.sh
cp -a "hosts/$(hostname -s)/config.yml" "hosts/$(hostname -s)/config.yml.bak.$(date +%Y%m%d-%H%M%S)"
```

If you install a new NIC or move a NIC to another motherboard slot, update the PCIe
ID and hardware inventory in the hostname config. The PCIe information can be
found with:

```bash
lspci -vv | grep Mellanox
```

## Redefining Which NIC A Camera Uses

For the step-by-step runbook, use
[`camera_nic_move_runbook.md`](camera_nic_move_runbook.md). The notes below are
the underlying configuration model.

For each moved camera:

1. Move the physical fiber/cable to the target NIC port.
2. Update that camera's `nic_port` in `hosts/$(hostname -s)/config.yml`.
3. Update that camera's `ip_address` so it is on the target NIC subnet.
4. Program the camera itself to use that IP address.
5. Apply and verify the host NetworkManager settings.

Use `camera_net_config.py` when moving a configured camera between managed NIC
ports. It plans the YAML update and, when run with `--apply`, can call Emergent
`evttools` before writing the config:

```bash
./camera_net_config.py move --camera 2010093 --nic mlnx1_p1_25g
sudo ./camera_net_config.py move --camera 2010093 --nic mlnx1_p1_25g --apply
```

After `evttools` exits, the wrapper probes the target camera IP with `arping`.
If the camera does not respond, the YAML config is left unchanged. If you already
used eCapture or another tool to program the camera IP, add
`--no-program-camera` and let the post-check verify reachability.

If the host-side optic moved with the camera cable, add
`--move-nic-transceiver`. If the old port is now empty, also add
`--retire-old-nic` so the vacated port is marked `managed: false` and
`expected_link: false`. If the old and target host-side optics were swapped, add
`--swap-nic-transceivers`.

To retire a port after a physical move:

```bash
./camera_net_config.py deactivate-nic --nic mlnx1_p4_25g
./camera_net_config.py deactivate-nic --nic mlnx1_p4_25g --apply
```

The lower-level host scripts configure host NICs only. `force_ip.cpp` is only a
temporary ForceIP example; it explicitly notes that the camera will revert on
reboot unless the persistent camera IP configuration is written.

## Step 1: Generate Persistent NIC Names

The naming script reads `hosts/$(hostname -s)/config.yml` and writes one systemd
`.link` file per NIC:

```text
/etc/systemd/network/10-pancakebatter-<name>.link
```

Preview the files first (needs no root, writes nothing):

```bash
./network_alias_assignment.sh --dry-run
```

Then apply them:

```bash
sudo ./network_alias_assignment.sh
```

Each file matches a NIC by its permanent MAC address and sets the configured name
and alias. For example:

```ini
[Match]
PermanentMACAddress=a0:88:c2:69:11:9e

[Link]
Name=mlnx1_p1_25g
AlternativeName=eth0
AlternativeNamesPolicy=database onboard slot path
```

`ip -d link show` then lists `altname eth0` plus the kernel's own `enp...` altname
(kept by `AlternativeNamesPolicy`). NICs with `altname: null` (onboard/management
ports) are left unchanged. Re-running the script replaces all earlier
`10-pancakebatter-*.link` files, so the config stays the source of truth.

Older versions wrote `NAME=` rules to `/etc/udev/rules.d/10-network-aliases.rules`
and `SYMLINK+=` aliases, which never created usable `ethN` names. The script renames
that file to `*.disabled-<date>` when it installs the new files, because the two
mechanisms would conflict.

If the links are needed in early boot, run `sudo update-initramfs -u` before
rebooting. Reboot after changing NIC names; a reboot is the cleanest way to make
sure NetworkManager sees the final names.

## Step 2: Create NetworkManager Profiles

After reboot, create any missing NetworkManager connection profiles for the named
interfaces:

```bash
sudo ./create_nm_connections.sh
```

This script is idempotent. It checks whether each configured interface exists and
whether a NetworkManager connection profile already exists before creating one.

## Step 2b: Keep DHCP Off the Camera Ports

NetworkManager automatically creates a DHCP "Wired connection N" profile for any Ethernet
port that has no profile, which must not happen on the static camera network (including
`spare` ports, which later become camera ports). Preview, then apply:

```bash
./disable_nm_autodhcp.py --dry-run
sudo ./disable_nm_autodhcp.py
```

For every NIC with role `camera` or `spare` it writes
`/etc/NetworkManager/conf.d/90-pancakebatter-no-auto-default.conf` (their MAC addresses under
`no-auto-default`) and deletes the generic profiles already created for them. Management and
`unknown` NICs, including the SSH port, are never listed or deleted, and the profiles this repo
creates (named after the interface) are never deleted. Re-run it after changing roles.

## Step 3: Apply Static NIC Settings

Apply the static IP, MTU, speed, and autonegotiation settings from the hostname
config:

```bash
sudo ./configure_interfaces.sh
```

The script uses `nmcli connection modify` to set:

- `ipv4.method manual`
- `ipv4.addresses`
- `802-3-ethernet.mtu`
- `802-3-ethernet.auto-negotiate`
- `802-3-ethernet.speed`
- full duplex when manually setting speed

It then reactivates modified NetworkManager connections and prints final
verification output using `nmcli`, `ip`, and `ethtool`.

## Step 4: Verify

Check host-side interface state:

```bash
ip -br link show
ip -br -4 addr show
nmcli device status
nmcli connection show
```

Check the configured camera NIC assignments and camera reachability:

```bash
sudo ./check_system.sh --only network --verbose
```

To run only the network checker directly:

```bash
sudo python3 ./check_network_settings.py --config "hosts/$(hostname -s)/config.yml" --verbose
```

For host-side validation without touching the cameras with ARP probes:

```bash
python3 ./check_network_settings.py --config "hosts/$(hostname -s)/config.yml" --skip-camera-arping --verbose
```

For a single camera, an ARP-level check is usually more useful than ping:

```bash
arping -c 3 192.168.110.2
```

Emergent SDK discovery is the next verification layer. Until it is wired into the
checker, run it manually:

```bash
sudo /opt/EVT/eSDK/tools/evttools -d
```

## Troubleshooting

If a script cannot find the config file, confirm that the file matches the short
hostname:

```bash
hostname -s
ls -l "hosts/$(hostname -s)/config.yml"
```

If `yq` is missing or does not support `yq e`, install the expected version:

```bash
sudo ./install_yq.sh
```

If `configure_interfaces.sh` cannot find a NetworkManager connection for an
interface, run `create_nm_connections.sh` first, then check:

```bash
nmcli device status
nmcli connection show
```

If a camera is not discovered or reachable after moving it, check these first:

- The camera's configured IP is on the same subnet as the target NIC host IP.
- The camera's persistent IP has actually been written to the camera.
- No two cameras or NICs share the same IP address.
- The physical link is up at the expected speed.
- No leftover netplan/systemd-networkd configuration is also trying to manage the same NIC.

## Legacy Note

Older versions of this document described manually editing netplan files with the
`networkd` renderer. That was the original approach, but the operational path in
this repo is now NetworkManager:

```text
hosts/$(hostname -s)/config.yml
  -> network_alias_assignment.sh
  -> create_nm_connections.sh
  -> configure_interfaces.sh
```
