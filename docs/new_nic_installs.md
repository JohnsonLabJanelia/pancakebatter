# Camera NIC Network Configuration

This repo now uses NetworkManager for camera NIC configuration. The earlier
netplan/systemd-networkd notes are historical; do not edit `/etc/netplan/*.yaml`
or run `netplan apply` for the camera NICs unless you are intentionally replacing
the current NetworkManager workflow.

The active machine config is named from the short hostname:

```bash
./$(hostname -s)_config.yml
```

For `pancake0`, that file is `pancake0_config.yml`. The network scripts derive this
path internally, so run them from the repo root.

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
      mac_address: a0:88:c2:69:11:9e
      mtu: 9000
      ip_address: 192.168.110.1/24
      link_settings:
        speed: 25000
        autoneg: false

cameras:
  E0-55-97-1E-AB-ED:
    serial_number: 2010093
    ip_address: 192.168.110.2
    nic_port: mlnx1_p1_25g
```

In this example, the host NIC is `192.168.110.1/24` and the attached camera is
`192.168.110.2`.

## Before Changing The Network

Back up both the host networking files and the repo config:

```bash
sudo ./network_backup.sh
cp -a "$(hostname -s)_config.yml" "$(hostname -s)_config.yml.bak.$(date +%Y%m%d-%H%M%S)"
```

If you install a new NIC or move a NIC to another motherboard slot, update the PCIe
ID and hardware inventory in the hostname config. The PCIe information can be
found with:

```bash
lspci -vv | grep Mellanox
```

## Redefining Which NIC A Camera Uses

For each moved camera:

1. Move the physical fiber/cable to the target NIC port.
2. Update that camera's `nic_port` in `$(hostname -s)_config.yml`.
3. Update that camera's `ip_address` so it is on the target NIC subnet.
4. Program the camera itself to use that IP address.
5. Apply and verify the host NetworkManager settings.

The repo scripts configure the host NICs only. They do not permanently write camera
IP settings into the cameras. Use eCapture's IP Configurator, or an eSDK tool using
`EVT_IPConfig()`, to make camera IP changes persistent. `force_ip.cpp` is only a
temporary ForceIP example; it explicitly notes that the camera will revert on reboot
unless the persistent camera IP configuration is written.

## Step 1: Generate Persistent NIC Names

The udev naming script reads `$(hostname -s)_config.yml` and writes:

```text
/etc/udev/rules.d/10-network-aliases.rules
```

Run:

```bash
sudo ./network_alias_assignment.sh
```

The generated rules map each NIC MAC address to the configured interface name and
alias. For example:

```udev
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:9e", NAME="mlnx1_p1_25g"
SUBSYSTEM=="net", ACTION=="add", ATTR{address}=="a0:88:c2:69:11:9e", SYMLINK+="eth0"
```

Reboot after changing NIC names. Reloading udev rules can work in some cases, but
a reboot is the cleanest way to make sure NetworkManager sees the final names.

## Step 2: Create NetworkManager Profiles

After reboot, create any missing NetworkManager connection profiles for the named
interfaces:

```bash
sudo ./create_nm_connections.sh
```

This script is idempotent. It checks whether each configured interface exists and
whether a NetworkManager connection profile already exists before creating one.

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
python3 ./check_network_settings.py "$(hostname -s)_config.yml" --verbose
```

For a single camera, an ARP-level check is usually more useful than ping:

```bash
arping -c 3 192.168.110.2
```

## Troubleshooting

If a script cannot find the config file, confirm that the file matches the short
hostname:

```bash
hostname -s
ls -l "$(hostname -s)_config.yml"
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
$(hostname -s)_config.yml
  -> network_alias_assignment.sh
  -> create_nm_connections.sh
  -> configure_interfaces.sh
```
