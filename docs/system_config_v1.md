# system_config v1

`system_config` v1 is the machine inventory and camera network assignment
contract used by the Pancake/Orange setup scripts.

The active host config lives in a per-host directory named from the short hostname:

```bash
./hosts/$(hostname -s)/config.yml
```

For `pancake0`, that is `hosts/pancake0/config.yml`. Set `PANCAKEBATTER_HOST=<name>`
to resolve another machine's directory (the scripts use `hostconfig.py` and
`lib/host_config.sh`). `system_config.example.yml` is the tracked example to copy
when adding a new machine by hand;
`capture_inventory.py` drafts one from the live hardware instead. The machine config declares its schema with:

```yaml
schema:
  name: system_config
  version: 1
```

The JSON Schema is tracked at:

```text
schemas/system_config.v1.schema.json
```

For the operational camera move checklist, see
[`camera_nic_move_runbook.md`](camera_nic_move_runbook.md).

## NIC Lifecycle Fields

Every NIC entry must declare these v1 fields:

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
```

`role` describes intended use. Current values are:

- `camera`: port is assigned to an Emergent camera.
- `spare`: hardware inventory or reserved subnet, but not currently used.
- `management`: non-camera host/admin network.
- `uplink`: upstream network.
- `unknown`: inventoried, purpose not decided yet.

`managed` controls whether the repo scripts should configure the port:

- `true`: create/check NetworkManager profile, static IP, MTU, and link settings.
- `false`: keep the port in inventory, but skip NetworkManager and live-link checks.

`expected_link` controls whether a physical carrier is expected during checks:

- `true`: link down is an error.
- `false`: link down is acceptable.

On `pancake0`, these fields reflect the current physical camera layout. Ports
with connected cameras should be active; empty ports should be kept as spares.

## Camera Assignments

Camera entries use their MAC address as the key. A camera must point to a
managed NIC through `nic_port`, and the camera IP must be inside that NIC's
subnet.

```yaml
cameras:
  E0-55-97-1E-AB-ED:
    serial_number: 2010093
    ip_address: 192.168.110.2
    nic_port: mlnx1_p1_25g
```

The host NIC is `192.168.110.1/24`; the attached camera is `192.168.110.2`.

## Script Behavior

`network_alias_assignment.sh` still names every listed NIC, including unmanaged
spares. Stable naming is useful for inventory and future activation.

`create_nm_connections.sh` and `configure_interfaces.sh` skip NICs where
`managed: false`.

`check_network_settings.py` validates the v1 metadata and the network subset of
the schema. It enforces operational checks only for `managed: true` NICs.

## Activating A Spare Port

Use `camera_net_config.py activate-nic` to put a spare port into service. It is
dry-run by default:

```bash
./camera_net_config.py activate-nic --nic mlnx2_p4_25g
```

To update the host config:

```bash
./camera_net_config.py activate-nic --nic mlnx2_p4_25g --apply
```

That sets the NIC to `role: camera`, `managed: true`, `expected_link: true`, and
`link_settings.autoneg: true` by default. Then run:

```bash
sudo ./create_nm_connections.sh
sudo ./configure_interfaces.sh
sudo ./check_system.sh --only network --verbose
```

The manual equivalent is:

1. Change `role` to `camera` or another active role.
2. Set `managed: true`.
3. Set `expected_link: true` if a cable/camera should be connected.
4. Confirm `ip_address`, `mtu`, and `link_settings`.
5. Point the camera's `nic_port` at that NIC and set the camera IP inside the NIC subnet.

## Moving A Camera

Use the runbook in [`camera_nic_move_runbook.md`](camera_nic_move_runbook.md)
for the complete physical move, eCapture fallback, and verification sequence.

Use `camera_net_config.py` to coordinate the camera IP assignment and the host
config update. It is dry-run by default:

```bash
./camera_net_config.py move --camera 2010093 --nic mlnx1_p1_25g
```

The wrapper resolves the camera by serial number or MAC address, checks that the
target NIC is managed, verifies the requested camera IP is inside the target NIC
subnet, and checks for duplicate camera IP/NIC assignments.

To apply the change and program the camera with Emergent `evttools`:

```bash
sudo ./camera_net_config.py move --camera 2010093 --nic mlnx1_p1_25g --apply
```

By default, the camera IP is inferred as the target host NIC IP plus one. For
example, `192.168.110.1/24` maps to camera IP `192.168.110.2`.

For v1, camera programming uses:

```bash
/opt/EVT/eSDK/tools/evttools -f <host_nic_ip> -o p
```

That `evttools` mode assigns persistent IPs starting at `<host_nic_ip> + 1`, so
the wrapper only allows this backend when the desired camera IP is that first
camera IP. After `evttools` exits, the wrapper probes the target IP with
`arping` before it writes YAML. This protects the config from being updated when
`evttools` returns success but did not actually find or program the target
camera.

If the camera has already been programmed some other way, update only the YAML
assignment with:

```bash
sudo ./camera_net_config.py move --camera 2010093 --nic mlnx1_p1_25g --no-program-camera --apply
```

Host-side optics are tracked on each NIC's `transceiver:` block. A camera move
does not assume the host-side optic moved with the cable. If the optic was moved
from the old NIC port to the target NIC port, include:

```bash
sudo ./camera_net_config.py move --camera 2010096 --nic mlnx2_p4_25g --move-nic-transceiver --retire-old-nic --apply
```

That moves the old NIC `transceiver:` block to the target NIC and clears the old
NIC transceiver fields to `null`. `--retire-old-nic` also marks the vacated port
as `role: spare`, `managed: false`, and `expected_link: false`.

If the old and target NIC optics were physically swapped, include:

```bash
sudo ./camera_net_config.py move --camera 2010096 --nic mlnx2_p4_25g --swap-nic-transceivers --apply
```

To retire an already-vacated port after the fact:

```bash
./camera_net_config.py deactivate-nic --nic mlnx1_p4_25g
./camera_net_config.py deactivate-nic --nic mlnx1_p4_25g --apply
```

## NIC naming convention

Camera/spare NICs follow the pancake0 convention, generated by `suggest_nic_layout.py`:

| Field | Rule |
| --- | --- |
| name | `mlnx<card>_p<port>_<speed>g`, e.g. `mlnx1_p1_25g` |
| card | 1 = the card physically nearest the CPU, counting outward. Confirm with `sudo ethtool -p <iface> 20` (blinks the port LED). Firmware slot names (`pcie_slot`) are reference only. |
| port | PCIe function + 1 (`p1` = function 0). Check against the bracket labels with `ethtool -p`. |
| altname | `eth0`, `eth1`, ... in card/port order; written as `AlternativeName=` in a systemd `.link` file |
| ip_address | `192.168.<110 + 10*n>.1/24` per port in card/port order; cameras take `.2` |
| link | MTU 9000, speed 25000, autoneg on |

Onboard/management NICs keep their kernel names (`altname: null`), and the naming script leaves them alone.

```bash
./capture_inventory.py
./suggest_nic_layout.py --card f1:00=1 --card 21:00=2 --camera-card 1   # dumpling
./gui_config_editor.py                                                  # review, add cameras
./network_alias_assignment.sh --dry-run                                 # preview the .link files
```

## `kernel_tuning` (optional)

Kernel-side tuning the acquisition pipeline depends on: required command-line
options, sysctl values, transparent-hugepage modes, per-camera-port MTU and
interrupt CPU sets, the isolated CPU list, and `core_roles`, a map from each
isolated CPU (string key) to its purpose (`role`, optional `consumer`,
`camera`, `sibling_of`, `note`). The schema validates the section's shape;
`check_kernel_tuning.py` compares it with the live host. See
[`kernel_tuning.md`](kernel_tuning.md).
