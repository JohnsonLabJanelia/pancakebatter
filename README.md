# pancakebatter

The purpose of this repository is to combine the documentation created by [Jinyao Yan, PhD](https://www.janelia.org/people/jinyao-yan) (Johnson Lab Research Associate) and [Ratan Othayoth, PhD](https://www.janelia.org/people/ratan-othayoth-0) (Johnson Lab Research Associate) into one document for new rigs built by Jeremy Delahanty for use in group leaders [Misha Ahrens, PhD](https://ahrenslab.org/) and [Rob Johnson, PhD](https://www.janelia.org/lab/johnson-lab) labs (but also those elsewhere in Janelia or even the world one day!).

A secondary goal is to introduce some basic automation into installing required packages, drivers, and static versioning for rig building in the lab. There will also be a standard directory structure built on the lab's server containing relevant files, documentation, and configuration files that are used for checking whether all installations meet a given known working configuration.

Longer term plans could include utilizing tools like [spack](https://spack.readthedocs.io/en/latest/index.html) and [Warewolf](https://warewolf.io/index.php) so its very well documented and maintained with infra as code. That's very low priority for now.

Check out the docs folder for documentation about how to use the scripts and what they are for.

Cross-repo ownership for machine inventory, orange runtime camera config, and citrus rig/canvas calibration is documented in [`docs/rig_canvas_contract.md`](docs/rig_canvas_contract.md).

Multi-user communal machine migration work is tracked in [`docs/shared_machine_setup_todo.md`](docs/shared_machine_setup_todo.md).

Per-machine files (`config.yml` inventory, driver-upgrade manifest) live in `hosts/<hostname>/`; scripts pick the directory from `hostname -s`, or from `PANCAKEBATTER_HOST`. To add a machine, run `./capture_inventory.py` on it to draft `hosts/<hostname>/config.captured.yml` (see [`hosts/README.md`](hosts/README.md)), or copy [`system_config.example.yml`](system_config.example.yml) by hand. See [`docs/system_config_v1.md`](docs/system_config_v1.md).

New cameras are discovered, given their port's camera IP, and recorded in the host config by `camera_adopt.py` (see [`docs/camera_adopt.md`](docs/camera_adopt.md)).

Apt package manifests used by the installer scripts live in [`packages/apt`](packages/apt/README.md). Conda environment files live in [`environments/`](environments/): `rig_control.yaml` for the rig control scripts and the optional `juicebox.yaml` for data analysis and plotting.

Operations: [`docs/camera_nic_reset.md`](docs/camera_nic_reset.md) (status and in-place firmware reset of the ConnectX camera NICs after the 2026-10-03 thermal shutdown), [`docs/rig_health_monitor.md`](docs/rig_health_monitor.md) (`rig_health_check.py` on a systemd timer that stays off the isolated cores), [`docs/ptp_services.md`](docs/ptp_services.md) (`ptp4l`/`phc2sys` as boot-time units via `install_ptp_units.sh`), and [`docs/rig_services_at_boot.md`](docs/rig_services_at_boot.md) (what comes back on its own after a boot and what to re-run after a config change).
