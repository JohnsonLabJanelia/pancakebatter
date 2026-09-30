# Shared Machine Setup TODO

This TODO tracks the migration from a single-user communal machine setup to a multi-user setup with cleaner ownership, permissions, and path boundaries.

Date started: 2026-03-17.

## Goal

Move shared rig operation away from treating `/home/jeremy` as the canonical machine identity.

Target outcome:

- each human operator has their own Unix account
- shared rig resources live in shared system locations, not one person's home directory
- long-lived configs and data are readable and writable through deliberate group ownership
- scripts and applications do not assume a specific username

## Why This Matters

Problems with the current pattern:

- poor accountability because multiple people may operate through one personal account
- shared machine behavior is coupled to one user's shell, home directory, Conda state, and SSH keys
- path assumptions like `/home/jeremy/...` make future handoff and machine rebuilds harder
- permissions are harder to reason about because "personal" and "shared" state are mixed together

## Current Pain Points Observed

Representative examples already present in the current repos:

- `pdu.py` used a personal Conda shebang (fixed 2026-09-29: now `#!/usr/bin/env python3`, run inside the `rig_control` env or set `RIG_CONTROL_PYTHON` for `reboot_cams.sh`)
- `environment.yaml` hard-coded a personal Conda prefix (fixed 2026-09-29: now `environments/rig_control.yaml` with no prefix)
- `orange-jeremy/src/orange.cpp` derives `orange_data` under `/home/<user>/orange_data`
- `citrus/scripts/system_checkup.py` defaults to `/home/jeremy/pancakebatter/system_config.yml`
- some docs and helper scripts still assume `/home/jeremy/orange_data/...`

This is workable for one person, but it is not a clean communal-machine boundary.

## Target State

Suggested baseline model:

- one account per person
- one shared operator/admin group for the rig
- optional service accounts for unattended processes
- shared code in a shared location such as `/srv/rig`, `/opt/rig`, or another agreed root
- shared data in a shared storage path such as `/mnt/Data1`, `/mnt/Data2`, or `/srv/data`
- machine-level configuration in `/etc`, or in one shared config root with explicit ownership
- runtime pointers and short-lived state in `/run` or `/var/lib`

## Phase 1: Decide The Operating Model

- [ ] Decide which users need login accounts on the machine.
- [ ] Decide which shared Unix group(s) should exist.
- [ ] Decide whether long-running processes need service accounts.
- [ ] Decide the canonical shared root for repos.
- [ ] Decide the canonical shared root for data and calibrations.
- [ ] Decide whether `system_config.yml` remains in a shared repo checkout or moves to a machine config path.
- [ ] Decide whether Orange runtime config should remain under a per-user home or move to a shared machine path.

## Phase 2: Inventory Current Single-User Assumptions

- [ ] Audit `pancakebatter` for hard-coded `/home/jeremy` paths.
- [ ] Audit `orange-jeremy` for hard-coded `/home/jeremy` and home-derived path assumptions.
- [ ] Audit `citrus` for hard-coded `/home/jeremy` and duplicate inventory/config assumptions.
- [ ] Inventory personal shell startup files, Conda envs, aliases, and SSH material that the rig currently depends on.
- [ ] Inventory systemd services, cron jobs, or desktop launchers that currently assume user `jeremy`.
- [ ] Inventory file ownership under the current shared data and repo directories.

## Phase 3: Create The Shared Layout

- [ ] Create individual user accounts for each operator.
- [ ] Create shared group(s) for rig operators and admins.
- [ ] Choose and create the shared repo root.
- [ ] Choose and create the shared config root.
- [ ] Choose and create the shared data root.
- [ ] Set group ownership and default permissions on those directories.
- [ ] Decide whether ACLs are needed in addition to Unix groups.

## Phase 4: Move Shared Assets Out Of `/home/jeremy`

- [ ] Move or re-clone shared repos into the agreed shared repo root.
- [ ] Move shared Orange runtime/config data into the agreed shared location if that is the chosen model.
- [ ] Move shared calibration artifacts into the agreed shared location.
- [ ] Move any machine-global scripts that should not depend on a personal home directory.
- [ ] Keep a temporary compatibility layer such as symlinks only if necessary during migration.

## Phase 5: Remove Username-Coupled Code Paths

- [ ] Replace hard-coded `/home/jeremy/...` paths in scripts and docs with configurable paths.
- [x] Remove personal Conda prefixes from committed environment files where possible. (`environments/rig_control.yaml`, `environments/juicebox.yaml`)
- [x] Replace personal shebangs with portable ones when appropriate. (`pdu.py`)
- [ ] Introduce environment variables or config keys for shared roots where the code currently assumes a home path.
- [ ] Make Citrus consume one authoritative machine inventory path instead of relying on a duplicate local copy.
- [ ] Review all repo docs for commands that assume user `jeremy`.

## Phase 6: Permissions And Operational Policy

- [ ] Set a default `umask` and group-write policy for shared directories.
- [ ] Decide which directories should be writable by all operators and which should be admin-only.
- [ ] Decide how secrets and credentials are stored without using a personal home directory as the system boundary.
- [ ] Decide how SSH access and sudo policy should work for operators versus admins.
- [ ] Document who owns upgrades, recovery, and machine-level configuration changes.

## Phase 7: Validation

- [ ] Verify a second user can log in and operate the rig without reading from `/home/jeremy`.
- [ ] Verify a second user can read and write the intended shared data/config directories.
- [ ] Verify Orange and Citrus still resolve their required config, output, and calibration paths.
- [ ] Verify machine checks still find `system_config.yml` at the intended canonical path.
- [ ] Verify no critical workflows depend on Jeremy's shell init, Conda base setup, or private files.
- [ ] Verify the machine can be administered even if the `jeremy` account is absent or disabled.

## Open Design Questions

- [ ] Should shared repos live under `/srv/rig`, `/opt/rig`, or on the data volume?
- [ ] Should `orange_data` become a machine-shared directory, or remain per-user with explicit export points?
- [ ] Should `system_config.yml` live in Pancakebatter only, or should it move to a machine config location and be mirrored into repo workflows?
- [ ] Which paths should become environment variables versus values in config files?
- [ ] Which workflows should become systemd services instead of user-launched shells?

## Done Criteria

- [ ] Two different human users can operate the machine through their own accounts.
- [ ] Shared rig workflows no longer require a personal home directory path.
- [ ] Shared repos, config, and data have explicit ownership and permissions.
- [ ] The machine's canonical operational state is documented and reproducible without relying on Jeremy's personal account.
