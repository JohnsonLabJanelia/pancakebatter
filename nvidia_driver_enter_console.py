#!/usr/bin/env python3
"""Back up pancake0's current driver state and stop its graphical desktop.

Run --check for read-only validation. Run --enter from a root Linux VT for
the first maintenance stage. This script never installs, uninstalls, unloads
modules, changes the default boot target, or reboots.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

BUNDLE = Path('/mnt/Data2/nvenc-driver-packages-20260919')
CATALOG_SHA256 = '3e6002f4cc4c68972595d11b3f3fe1c31b8731cb4a24b4763f88726d31539961'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LC_ALL': 'C', 'LANG': 'C'}
AUTOMATION = ('apt-daily.timer', 'apt-daily-upgrade.timer', 'apt-daily.service',
              'apt-daily-upgrade.service', 'unattended-upgrades.service')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def run(*argv, accepted=(0,), timeout=120):
    result = subprocess.run(argv, env=ENV, text=True, capture_output=True,
                            timeout=timeout)
    require(result.returncode in accepted,
            f'{argv[0]} failed ({result.returncode}): {result.stderr.strip()} {result.stdout.strip()}')
    return result


def service_state(unit):
    return run('systemctl', 'show', unit, '-p', 'ActiveState', '--value').stdout.strip()


def compute_idle():
    result = run('nvidia-smi', '--query-compute-apps=pid,process_name',
                 '--format=csv,noheader,nounits')
    require(not result.stdout.strip(), 'GPU compute applications remain: ' + result.stdout)


def package_manager_idle():
    processes = run('ps', '-eo', 'comm=').stdout.splitlines()
    # Linux comm is truncated to 15 characters.
    blocked = {name[:15] for name in ('apt', 'apt-get', 'dpkg', 'nvidia-installer', 'nvidia-uninstall')}
    busy = sorted(set(processes) & blocked)
    require(not busy, 'Package/driver maintenance is already running: ' + ', '.join(busy))


def package_locks_unused():
    paths = [str(p) for p in (Path('/var/lib/dpkg/lock'), Path('/var/lib/dpkg/lock-frontend')) if p.exists()]
    require(bool(paths), 'dpkg lock files are missing')
    users = run('fuser', *paths, accepted=(0, 1))
    require(users.returncode == 1 and not users.stdout.strip() and not users.stderr.strip(),
            'Package lock users or inspection error remain: ' + users.stdout + users.stderr)


def check():
    require(run('findmnt', '-n', '-o', 'UUID', '--target', str(BUNDLE)).stdout.strip()
            == 'bb4ea7ac-11f3-4f20-80b1-5e3cb0927711', 'Unexpected Data2 filesystem')
    require(sha256(BUNDLE / 'SHA256SUMS') == CATALOG_SHA256, 'Bundle checksum catalog changed')
    for line in (BUNDLE / 'SHA256SUMS').read_text().splitlines():
        digest, relative = line.split('  ', 1)
        p = Path(relative)
        require(not p.is_absolute() and '..' not in p.parts, 'Invalid bundle path')
        require(sha256(BUNDLE / p) == digest, f'Bundle file changed: {relative}')
    manifest = json.loads((BUNDLE / 'pancakebatter-source/configs/nvidia_driver_upgrade.pancake0.json').read_text())
    expected = manifest['expected']
    recovery = manifest['recovery']
    require(sha256(Path(recovery['rollback_installer'])) == recovery['rollback_installer_sha256'],
            'Rollback installer checksum mismatch')
    require(Path(recovery['backup_image']).is_dir(), 'Clonezilla image directory missing')
    require(run('uname', '-r').stdout.strip() == expected['kernel'], 'Kernel changed')
    require(str(Path('/usr/local/cuda').resolve()) == expected['cuda_symlink_target'], 'CUDA default changed')
    require(Path(expected['tensorrt_root']).is_dir(), 'TensorRT directory missing')
    require(run('dpkg-query', '-W', '-f=${Version}', 'dkms').stdout == expected['dkms_version'], 'DKMS changed')
    require(run('dpkg-query', '-W', '-f=${Version}', 'mlnx-ofed-kernel-dkms').stdout
            == expected['ofed_package_version'], 'OFED changed')
    require(run('systemctl', 'get-default').stdout.strip() == 'graphical.target', 'Default boot target changed')
    names = run('systemctl', 'show', 'display-manager.service', '-p', 'Names', '--value').stdout.split()
    require('gdm3.service' in names, 'Unexpected display manager')
    require(service_state('openibd.service') == 'active', 'OFED service is not active')
    gpus = run('nvidia-smi', '--query-gpu=uuid,index,name,driver_version,pci.bus_id', '--format=csv,noheader').stdout
    rows = [[field.strip() for field in row] for row in csv.reader(gpus.splitlines())]
    require(len(rows) == 9 and {row[0] for row in rows} == set(expected['gpu_uuids']), 'GPU inventory changed')
    require({row[3] for row in rows} == {'535.183.06'}, 'Driver version changed')
    for module in ('nvidia', 'nvidia_peermem'):
        require(run('modinfo', '-F', 'version', module).stdout.strip() == '535.183.06', f'{module} version changed')
    dkms = run('dkms', 'status')
    nvidia_rows = [row for row in dkms.stdout.splitlines() if row.startswith('nvidia/')]
    require(not dkms.stderr.strip() and nvidia_rows ==
            [f'nvidia/535.183.06, {expected["kernel"]}, x86_64: installed'], 'NVIDIA DKMS registration changed')
    loaded = {row.split()[0] for row in Path('/proc/modules').read_text().splitlines()}
    require({'nvidia', 'nvidia_peermem'} <= loaded and 'nv_peer_mem' not in loaded,
            'Loaded NVIDIA/peer-memory module state changed')
    for module in ('nvidia', 'nvidia_peermem'):
        require(Path(f'/sys/module/{module}/version').read_text().strip() == '535.183.06',
                f'Loaded {module} version changed')
    require(not Path('/var/lib/dkms/nvidia-fs').exists(), 'Stale GDS registration returned')
    compute_idle()
    package_manager_idle()
    return {'gpu_inventory': gpus, 'dkms_status': dkms.stdout, 'kernel': expected['kernel'],
            'bundle_catalog_sha256': CATALOG_SHA256, 'checks_passed': True}


def process_identity(pid):
    """Read kernel process ancestry and controlling-terminal metadata only."""
    process = Path('/proc') / str(pid)
    raw = (process / 'stat').read_text()
    closing = raw.rfind(')')
    fields = raw[closing + 2:].split()
    tty_device = int(fields[4]) & 0xffffffff
    major, minor = os.major(tty_device), os.minor(tty_device)
    uid_line = next(line for line in (process / 'status').read_text().splitlines()
                    if line.startswith('Uid:'))
    return {'pid': pid, 'ppid': int(fields[1]),
            'comm': raw[raw.find('(') + 1:closing], 'euid': int(uid_line.split()[2]),
            'console': f'/dev/tty{minor}' if major == 4 and 1 <= minor <= 63 else None}


def console_origin(terminal, pid=None):
    """Accept a direct VT or a sudo PTY whose live ancestry proves a VT login.

    SUDO_TTY and XDG_SESSION_ID are deliberately not used as proof. In a
    desktop or SSH PTY, the process chain does not reach a root VT login.
    """
    if re.fullmatch(r'/dev/tty(?:[1-9]|[1-5][0-9]|6[0-3])', terminal):
        return {'console': terminal, 'stdin_terminal': terminal, 'via_sudo_pty': False}
    require(re.fullmatch(r'/dev/pts/[0-9]+', terminal) is not None,
            f'Expected a Linux VT or its sudo PTY; received {terminal}')
    current = os.getpid() if pid is None else pid
    seen = set()
    consoles = set()
    sudo_seen = False
    try:
        for _ in range(64):
            if current <= 1 or current in seen:
                break
            seen.add(current)
            process = process_identity(current)
            if process['console']:
                consoles.add(process['console'])
            if process['comm'] == 'sudo' and process['euid'] == 0:
                sudo_seen = True
            if process['comm'] == 'login' and process['euid'] == 0 and process['console']:
                require(sudo_seen and consoles == {process['console']},
                        'Linux VT ancestry does not match a sudo console login')
                return {'console': process['console'], 'stdin_terminal': terminal,
                        'via_sudo_pty': True, 'login_pid': current}
            current = process['ppid']
    except (OSError, ValueError, IndexError, StopIteration) as exc:
        raise RuntimeError(f'Could not verify Linux VT ancestry for {terminal}: {exc}') from exc
    raise RuntimeError(f'Expected a Linux VT login behind {terminal}; console ancestry was not found')


def enter():
    require(os.geteuid() == 0, 'Run --enter from the root console')
    require(sys.stdin.isatty(), 'Run directly from a Linux text console')
    terminal = os.ttyname(sys.stdin.fileno())
    origin = console_origin(terminal)
    os.umask(0o077)
    print('Verifying the frozen bundle, recovery installer, and current host...', flush=True)
    baseline = check()
    print('Preflight passed. Creating a private backup of live driver state...', flush=True)
    evidence = Path(tempfile.mkdtemp(prefix='nvenc-driver-console-entry-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-', dir='/mnt/Data2'))
    # Only the sanitized status is readable without root; archives remain 0600.
    evidence.chmod(0o711)
    status = {'stage': 'backing_up', 'evidence': str(evidence), 'console': origin['console'],
              'console_origin': origin,
              'driver_version': '535.183.06', 'driver_changed': False,
              'services_stop_requested': [], 'backup_verified': False}

    def save_status():
        try:
            status['service_states'] = {unit: service_state(unit) for unit in ('gdm3.service', *AUTOMATION)}
        except Exception as exc:
            status['service_state_inspection_error'] = str(exc)
        path = evidence / 'status.json'
        pending = evidence / '.status.pending'
        pending.write_text(json.dumps(status, indent=2) + '\n')
        pending.chmod(0o644)
        pending.replace(path)

    save_status()
    try:
        (evidence / 'baseline.json').write_text(json.dumps(baseline, indent=2) + '\n')
        states = {unit: service_state(unit) for unit in ('gdm3.service', *AUTOMATION)}
        (evidence / 'services-before.json').write_text(json.dumps(states, indent=2) + '\n')
        # Refuse active package work; stop its timers before the state snapshot.
        for unit in ('apt-daily.service', 'apt-daily-upgrade.service'):
            require(states[unit] not in ('active', 'activating', 'deactivating'), f'{unit} is busy')
        package_manager_idle()
        status['services_stop_requested'].extend(AUTOMATION)
        save_status()
        run('systemctl', 'stop', *AUTOMATION)
        package_manager_idle()
        package_locks_unused()
        paths = ['var/lib/dkms', 'boot', 'var/lib/dpkg/status', 'etc/apt']
        paths += [name for name in ('var/lib/nvidia', 'etc/dkms') if (Path('/') / name).is_dir()]
        size = int(run('du', '-scb', *(str(Path('/') / name) for name in paths)).stdout.splitlines()[-1].split()[0])
        require(shutil.disk_usage(evidence).free > size + 2 * 1024**3, 'Insufficient Data2 space for the backup')
        archive = evidence / 'driver-state-before.tar'
        for operation in ('--create', '--compare'):
            with (evidence / ('backup-' + operation[2:] + '.log')).open('w') as log:
                command = ['tar', '--acls', '--xattrs', '--numeric-owner', '-C', '/',
                           operation, '--file', str(archive)]
                if operation == '--create':
                    command.extend(paths)
                result = subprocess.run(command, env=ENV, stdout=log, stderr=subprocess.STDOUT)
                require(result.returncode == 0, f'Backup {operation} failed; inspect {log.name}')
        digest = sha256(archive)
        (evidence / 'driver-state-before.tar.sha256').write_text(f'{digest}  driver-state-before.tar\n')
        status.update(backup_verified=True, backup_sha256=digest, backup_bytes=archive.stat().st_size)
        for name in ('package-lock.json', 'package-lock.tsv', 'SHA256SUMS'):
            shutil.copyfile(BUNDLE / name, evidence / name)
        installer_log = Path('/var/log/nvidia-installer.log')
        if installer_log.is_file():
            shutil.copyfile(installer_log, evidence / 'nvidia-installer-before.log')
        print('Backup verified. Stopping the graphical desktop...', flush=True)
        compute_idle()
        status['services_stop_requested'].append('gdm3.service')
        save_status()
        run('systemctl', 'stop', 'gdm3.service')
        status['stage'] = 'desktop_stopped'
        save_status()
        require(service_state('gdm3.service') == 'inactive', 'Display manager is not inactive')
        compute_idle()
        devices = sorted(set(Path('/dev').glob('nvidia*')) | set(Path('/dev/nvidia-caps').glob('*'))
                         | set(Path('/dev/dri').glob('card*')) | set(Path('/dev/dri').glob('renderD*')))
        devices = [str(path) for path in devices if path.is_char_device()]
        require(bool(devices), 'No GPU device nodes found')
        users = run('fuser', *devices, accepted=(0, 1))
        require(users.returncode == 1 and not users.stdout.strip() and not users.stderr.strip(),
                'GPU device users or inspection error remain: ' + users.stdout + users.stderr)
        require(service_state('openibd.service') == 'active', 'OFED service state changed')
        require(run('systemctl', 'get-default').stdout.strip() == 'graphical.target', 'Saved boot target changed')
        package_manager_idle()
        package_locks_unused()
        status['stage'] = 'ready_for_driver_unload'
        save_status()
        print(f'CONSOLE ENTRY COMPLETE\nEvidence: {evidence}\nReturn to Codex with Ctrl+Alt+F3.', flush=True)
        return 0
    except Exception as exc:
        status.update(stage='stopped_for_review', error=str(exc))
        save_status()
        print(f'STOPPED: {exc}\nEvidence: {evidence}\nReturn to Codex with Ctrl+Alt+F3 for review.', file=sys.stderr)
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Read-only validation; no backup or service changes')
    mode.add_argument('--enter', action='store_true', help='Root VT only: back up state and stop desktop/package automation')
    args = parser.parse_args()
    try:
        if args.check:
            print(json.dumps(check(), indent=2))
            return 0
        return enter()
    except Exception as exc:
        print(f'STOPPED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
