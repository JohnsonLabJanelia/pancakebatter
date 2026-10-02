# Camera NIC Move Runbook

Use this runbook when moving an existing Emergent camera from one camera NIC port
to another on the same host.

The active machine config is:

```bash
./hosts/$(hostname -s)/config.yml
```

For `pancake0`, that is `hosts/pancake0/config.yml`.

## Inputs

Before starting, decide:

- Camera serial number or MAC address.
- Target NIC name.
- Whether the host-side optic/transceiver physically moves with the cable.
- Whether the old NIC port should be retired after the move.

The normal target camera IP is the target host NIC IP plus one. For example,
host NIC `192.168.170.1/24` maps to camera `192.168.170.2`.

`camera_net_config.py --apply` creates a timestamped backup of the active config
before writing changes.

## 1. Activate The Target NIC

If the target NIC is a spare, activate it before moving the camera:

```bash
./camera_net_config.py activate-nic --nic <target_nic>
./camera_net_config.py activate-nic --nic <target_nic> --apply
sudo ./create_nm_connections.sh
sudo ./configure_interfaces.sh
```

The dry run should show the target NIC changing to:

```text
role: camera
managed: true
expected_link: true
```

## 2. Dry Run The Camera Move

Run the move without `--apply`:

```bash
./camera_net_config.py move --camera <serial_or_mac> --nic <target_nic> --move-nic-transceiver --retire-old-nic
```

Use `--move-nic-transceiver` when the old host-side optic physically moves to
the target NIC port. Use `--swap-nic-transceivers` only when the old and target
host-side optics were physically swapped. Omit both flags if the host-side optics
did not move.

Use `--retire-old-nic` when the old NIC port is now empty. It marks the vacated
port as:

```text
role: spare
managed: false
expected_link: false
```

Confirm the dry run shows the expected old NIC, new NIC, old camera IP, new
camera IP, and transceiver action.

## 3. Move The Hardware

Move the camera cable/fiber to the target NIC port.

If the host-side optic moves with the cable, move that optic too. This physical
action is separate from the camera transceiver inventory recorded under each
camera.

## 4. Try The Wrapper Apply

Run the move with `--apply`:

```bash
sudo ./camera_net_config.py move --camera <serial_or_mac> --nic <target_nic> --move-nic-transceiver --retire-old-nic --apply
```

The wrapper calls:

```bash
/opt/EVT/eSDK/tools/evttools -f <target_host_nic_ip> -o p
```

Then it probes the target camera IP with `arping`. The YAML config is updated
only if the camera responds. This prevents recording a move when `evttools`
returns success but did not actually discover or program the camera.

## 5. eCapture Fallback

If `evttools` does not program the camera, use eCapture to set the camera IP
manually.

Set:

```text
IP:       <target_host_nic_ip + 1>
Netmask:  255.255.255.0
Gateway:  blank / 0.0.0.0
NIC:      <target_nic> / <target_host_nic_ip>
```

After eCapture confirms the camera is reachable, update only the YAML and
inventory:

```bash
sudo ./camera_net_config.py move --camera <serial_or_mac> --nic <target_nic> --move-nic-transceiver --retire-old-nic --no-program-camera --apply
```

Keep the same transceiver flag used in the dry run.

## 6. Clear The Vacated NIC Runtime State

After the config marks the old NIC as retired, disconnect the old NetworkManager
device so it is not left as an active camera port:

```bash
sudo nmcli device disconnect <old_nic>
```

Use the exact old NIC shown in the move plan. Do not disconnect the target NIC.

## 7. Verify

Run the repository network check:

```bash
sudo ./check_system.sh --only network --verbose
```

Also verify SDK discovery:

```bash
sudo /opt/EVT/eSDK/tools/evttools -d
```

For a single camera, verify ARP directly:

```bash
sudo arping -c 5 -I <target_nic> <target_camera_ip>
```

The check should show:

- Target NIC is `role=camera managed=true expected_link=true`.
- Target NIC has the expected static host IP.
- Target NIC link is detected at the expected speed.
- Camera IP is on the target NIC subnet.
- Camera is reachable through the target NIC.
- Old NIC is `role=spare managed=false expected_link=false` when retired.

If verification passes, commit the config and script/doc changes that should
become the new source of truth.

## Troubleshooting

If `camera_net_config.py move` says the target NIC has `managed=false`, activate
the target NIC first and rerun `create_nm_connections.sh` and
`configure_interfaces.sh`.

If `evttools` finds the other cameras but not the moved camera, use the eCapture
fallback. The wrapper should leave YAML unchanged in this case.

If the checker reports the old NIC has no link but `expected_link=true`, retire
that port:

```bash
./camera_net_config.py deactivate-nic --nic <old_nic>
./camera_net_config.py deactivate-nic --nic <old_nic> --apply
sudo nmcli device disconnect <old_nic>
```

If ARP fails but the camera appears in eCapture, close or refresh eCapture and
rerun the ARP/check command. Camera discovery can be transient after network
churn.

## pancake0 Example

Moving camera `2010095` to `mlnx2_p3_25g`:

```bash
./camera_net_config.py activate-nic --nic mlnx2_p3_25g --apply
sudo ./create_nm_connections.sh
sudo ./configure_interfaces.sh
./camera_net_config.py move --camera 2010095 --nic mlnx2_p3_25g --move-nic-transceiver --retire-old-nic
```

After physically moving the cable and host-side optic:

```bash
sudo ./camera_net_config.py move --camera 2010095 --nic mlnx2_p3_25g --move-nic-transceiver --retire-old-nic --apply
```

If `evttools` does not program it, set camera `2010095` to `192.168.170.2` in
eCapture, then run:

```bash
sudo ./camera_net_config.py move --camera 2010095 --nic mlnx2_p3_25g --move-nic-transceiver --retire-old-nic --no-program-camera --apply
sudo nmcli device disconnect mlnx1_p3_25g
sudo ./check_system.sh --only network --verbose
```
