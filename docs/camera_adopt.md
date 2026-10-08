# Adopting a new camera

`camera_adopt.py` takes a camera that is plugged into a camera NIC port, gives it the port's
camera IP persistently, and records it under `cameras:` in `hosts/<hostname>/config.yml`.

```bash
./camera_adopt.py                                          # preview (read-only)
sudo "$(command -v python3)" ./camera_adopt.py --apply     # program the camera, verify, write config
sudo "$(command -v python3)" ./camera_adopt.py --apply --reboot-check   # also reboot it to prove the IP persists
```

Use the `sudo "$(command -v python3)"` form so root runs the same Python (with PyYAML) as you.

## What it does

1. **Discover** with `camera_discover` (a read-only eSDK probe built from `camera_discover.cpp` into the
   ignored `build/` directory on first use). It uses broadcast discovery, so cameras whose IP is outside
   the port's subnet are found. Each result includes the host NIC that saw it: that is the port.
2. **Plan** the camera IP as the port's host IP + 1 (`192.168.110.1/24` -> `192.168.110.2`).
3. **Program** with the vendor tool: `evttools -f <port_ip> -o bp` (force the IP, then persist it).
   `evttools` is a thin wrapper around the same eSDK calls (`EVT_ForceIPEx`, `EVT_IPConfig`).
4. **Verify** by rediscovering on the port: the camera must answer at the new IP and report a persistent IP.
5. **Record** the entry (MAC key, model, serial, IP, `nic_port`) with a validated, backed-up save.

Only cameras programmed in the run are verified and reboot-checked; cameras that are already right
are listed and left alone. `--reboot-check` uses Orange's `evt_force_reboot` (found under
`$ORANGE_CAMERA_READY_ROOT/targets/release/`, or `--reboot-tool`), since `evttools` 2.55.02 has no
reboot command; without the tool the check is skipped with a note, and the camera's own
`persistent_ip_active` flag from discovery is the evidence that the IP survives a reboot.
   If any step fails, the config is not touched. Lens details are not reported by cameras: add them in
   `gui_config_editor.py`.

## When it refuses (nothing changes)

- The camera is on a port that is not a managed `camera` NIC (use `camera_net_config.py activate-nic`).
- Two cameras are on one port: `evttools -f` would force both, so connect one at a time.
- The camera is already configured on a different port (use `camera_net_config.py move`).
- The target IP conflicts with another configured camera.
