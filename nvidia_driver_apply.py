#!/usr/bin/env python3
"""Apply pancake0's frozen 535-to-610 migration from its verified root console.

Requires the successful console-entry backup. Stops before reboot. On failure,
preserves evidence and stops for review rather than improvising package repair.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import nvidia_driver_enter_console as entry

BUNDLE = entry.BUNDLE
KERNEL = '6.5.0-44-generic'
TARGET = '610.57.04'
MODULES = ('nvidia_peermem', 'nvidia_uvm', 'nvidia_drm', 'nvidia_modeset', 'nvidia')
PREFERENCE = Path('/etc/apt/preferences.d/nvenc-driver-610-exact.pref')
ENV = {**entry.ENV, 'CC': '/usr/bin/gcc-12', 'TERM': os.environ.get('TERM', 'linux')}
require = entry.require


def command(*argv, accepted=(0,), env=None):
    p = subprocess.run(argv, env=env or ENV, capture_output=True, text=True)
    require(p.returncode in accepted, f'{argv[0]} failed ({p.returncode}): {p.stderr.strip()} {p.stdout.strip()}')
    return p


def loaded_modules():
    return {row.split()[0] for row in Path('/proc/modules').read_text().splitlines()}


def no_gpu_clients():
    # Call only before unloading; nvidia-smi would otherwise load the driver again.
    entry.compute_idle()
    devices = set(Path('/dev').glob('nvidia*')) | set(Path('/dev/nvidia-caps').glob('*'))
    devices |= set(Path('/dev/dri').glob('card*')) | set(Path('/dev/dri').glob('renderD*'))
    paths = sorted(str(p) for p in devices if p.is_char_device())
    require(bool(paths), 'GPU device nodes missing before unload')
    result = command('fuser', *paths, accepted=(0, 1))
    require(result.returncode == 1 and not result.stdout.strip() and not result.stderr.strip(),
            'GPU device users or inspection error remain: ' + result.stdout + result.stderr)


def quiet_services():
    for unit in ('gdm3.service', *entry.AUTOMATION):
        require(entry.service_state(unit) == 'inactive', f'{unit} must remain inactive')
    require(entry.service_state('openibd.service') == 'active', 'OFED service is not active')
    entry.package_manager_idle()
    entry.package_locks_unused()


def select_evidence():
    candidates = []
    for directory in Path('/mnt/Data2').glob('nvenc-driver-console-entry-*'):
        path = directory / 'status.json'
        if directory.is_symlink() or not directory.is_dir() or directory.stat().st_uid != 0:
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_uid != 0:
            continue
        status = json.loads(path.read_text())
        if status.get('stage') == 'ready_for_driver_unload' and status.get('backup_verified') is True:
            require(status.get('evidence') == str(directory), 'Console-entry evidence path mismatch')
            candidates.append((directory, status))
    require(len(candidates) == 1, f'Expected one successful console-entry backup; found {len(candidates)}')
    evidence, status = candidates[0]
    archive = evidence / 'driver-state-before.tar'
    require(not archive.is_symlink() and archive.is_file() and archive.stat().st_uid == 0,
            'Expected root-owned backup archive')
    require(archive.stat().st_size == status['backup_bytes'] and entry.sha256(archive) == status['backup_sha256'],
            'Live driver backup checksum changed')
    return evidence, status


def verify_simulation(stdout, locked):
    expected = {(row['package'], row['version'], row['architecture']) for row in locked}
    require(len(locked) == len(expected) == 22, 'Target lock must contain exactly 22 packages')
    pattern = re.compile(r'^Inst\s+(\S+?)(?::\S+)?(?:\s+\[[^]]+\])?\s+\((\S+).*\s+\[([^]]+)\]\)$')
    installed = []
    configured = []
    for line in stdout.splitlines():
        require(not line.startswith(('Remv ', 'Purg ')), 'Unexpected removal in APT simulation')
        if line.startswith('Inst '):
            match = pattern.fullmatch(line)
            require(match is not None, 'Unrecognized APT install line: ' + line)
            installed.append(match.groups())
        if line.startswith('Conf '):
            match = re.fullmatch(r'Conf\s+(\S+?)(?::\S+)?\s+\((\S+).*\s+\[([^]]+)\]\)', line)
            require(match is not None, 'Unrecognized APT configure line: ' + line)
            configured.append(match.groups())
    require(len(installed) == 22 and set(installed) == expected, 'APT install set differs from the frozen 22 packages')
    require(len(configured) == 22 and set(configured) == expected, 'APT configure set differs from the frozen 22 packages')


def prepare_apt(root, locked):
    root.mkdir(mode=0o700, exist_ok=False)
    for relative in ('apt.conf.d', 'sources.list.d', 'preferences.d', 'lists/partial', 'cache/archives/partial', 'logs'):
        (root / relative).mkdir(parents=True, exist_ok=True)
    for relative in ('apt-main.conf', 'sources.list', 'preferences'):
        (root / relative).touch()
    (root / 'apt-bootstrap.conf').write_text(
        f'Dir::Etc::main "{root / "apt-main.conf"}";\n'
        f'Dir::Etc::parts "{root / "apt.conf.d"}";\n')
    env = {**ENV, 'APT_CONFIG': str(root / 'apt-bootstrap.conf'), 'DEBIAN_FRONTEND': 'noninteractive'}
    audit = command('apt-config', 'dump', env=env).stdout
    (root / 'apt-config.txt').write_text(audit)
    require(not any(line.startswith(('DPkg::Pre-Invoke', 'DPkg::Post-Invoke', 'DPkg::Pre-Install-Pkgs',
                                     'APT::Update::Post-Invoke')) for line in audit.splitlines()), 'APT inherited hooks')
    debs = []
    for row in locked:
        relative = Path(row['filename'])
        require(relative.parent == Path('packages/target') and relative.suffix == '.deb', 'Unexpected payload path')
        source = BUNDLE / relative
        require(source.stat().st_size == row['size'] and entry.sha256(source) == row['sha256'], 'Payload checksum changed')
        cached = root / 'cache/archives' / source.name
        shutil.copyfile(source, cached)
        require(entry.sha256(cached) == row['sha256'], 'Private APT cache copy checksum mismatch')
        debs.append(str(cached))
    options = {
        'Dir::Etc::sourcelist': root / 'sources.list', 'Dir::Etc::sourceparts': root / 'sources.list.d',
        'Dir::Etc::preferences': root / 'preferences', 'Dir::Etc::preferencesparts': root / 'preferences.d',
        'Dir::State::lists': root / 'lists', 'Dir::State::status': '/var/lib/dpkg/status',
        'Dir::Cache': root / 'cache', 'Dir::Cache::archives': root / 'cache/archives', 'Dir::Log': root / 'logs',
    }
    argv = ['apt-get']
    for key, value in options.items():
        argv.extend(('-o', f'{key}={value}'))
    argv.extend(('-o', 'Dpkg::Options::=--force-confold', '--no-download', '--no-remove', '--no-install-recommends'))
    return argv, debs, env


def simulate(argv, debs, env, locked, log):
    result = command(*argv, '--simulate', 'install', *debs, env=env)
    log.write_text(result.stdout + result.stderr)
    verify_simulation(result.stdout, locked)


def install(argv, debs, env, log):
    # This exact, already-reviewed package set is authorized by --apply.
    with log.open('w') as output:
        process = subprocess.Popen([*argv, '--assume-yes', 'install', *debs], env=env,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in process.stdout:
            print(line, end='', flush=True)
            output.write(line)
            output.flush()
        require(process.wait() == 0, f'APT failed; inspect {log}')


def installed_checks(evidence, locked):
    audit = command('dpkg', '--audit')
    (evidence / 'dpkg-audit-after.txt').write_text(audit.stdout + audit.stderr)
    require(not audit.stdout.strip() and not audit.stderr.strip(), 'dpkg audit is not clean')
    for row in locked:
        result = command('dpkg-query', '-W', '-f=${db:Status-Abbrev}\t${Version}\t${Architecture}', row['package'])
        require(result.stdout == f'ii \t{row["version"]}\t{row["architecture"]}', 'Installed package mismatch: ' + row['package'])
    dkms = command('dkms', 'status')
    (evidence / 'dkms-after.txt').write_text(dkms.stdout + dkms.stderr)
    require(not dkms.stderr.strip(), 'DKMS reported errors')
    previous_dkms = json.loads((evidence / 'baseline.json').read_text())['dkms_status']
    require({row for row in dkms.stdout.splitlines() if not row.startswith('nvidia/')} ==
            {row for row in previous_dkms.splitlines() if not row.startswith('nvidia/')},
            'A non-NVIDIA DKMS registration changed')
    nvidia = [row for row in dkms.stdout.splitlines() if row.startswith('nvidia/')]
    require(nvidia == [f'nvidia/{TARGET}, {KERNEL}, x86_64: installed'], 'Target NVIDIA DKMS registration mismatch')
    require(f'mlnx-ofed-kernel/24.01.OFED.24.01.0.3.3.1, {KERNEL}, x86_64: installed' in dkms.stdout, 'OFED DKMS changed')
    modules = {}
    for module in MODULES:
        fields = {field: command('modinfo', '-k', KERNEL, '-F', field, module).stdout.strip()
                  for field in ('version', 'vermagic', 'filename', 'depends', 'license')}
        require(fields['version'] == TARGET and fields['vermagic'].split()[0] == KERNEL, f'{module} build mismatch')
        require('/updates/dkms/' in fields['filename'], f'{module} is outside the DKMS module directory')
        modules[module] = fields
    require(modules['nvidia']['license'] == 'NVIDIA', 'Expected proprietary NVIDIA module flavor')
    require({'nvidia', 'ib_core'} <= set(modules['nvidia_peermem']['depends'].split(',')), 'Peer-memory module lacks OFED linkage')
    (evidence / 'module-verification.json').write_text(json.dumps(modules, indent=2) + '\n')
    initrd = evidence / 'initrd-610'
    command('unmkinitramfs', f'/boot/initrd.img-{KERNEL}', str(initrd))
    for path in initrd.rglob('nvidia*.ko*'):
        if '/updates/dkms/' in str(path) and path.is_file():
            require(command('modinfo', '-F', 'version', str(path)).stdout.strip() == TARGET, 'Stale NVIDIA module in initramfs')
    require(not set(MODULES) & loaded_modules(), 'NVIDIA modules loaded before planned reboot')
    require('nv_peer_mem' not in loaded_modules(), 'Legacy peer-memory module is loaded')
    require(entry.service_state('gdm3.service') == 'inactive', 'Desktop started unexpectedly')
    require(entry.service_state('openibd.service') == 'active', 'OFED service changed')
    require(command('systemctl', 'get-default').stdout.strip() == 'graphical.target', 'Default boot target changed')
    require(command('systemctl', 'is-enabled', 'dkms.service', accepted=(0, 1, 3, 4)).stdout.strip() == 'disabled', 'Unexpected DKMS boot service enablement')
    require(Path('/run/systemd/system/nvidia-persistenced.service').is_symlink()
            and Path('/run/systemd/system/nvidia-persistenced.service').resolve() == Path('/dev/null'), 'Persistence runtime mask missing')
    require(str(Path('/usr/local/cuda').resolve()) == '/usr/local/cuda-12.2', 'CUDA default changed')
    require(Path('/usr/local/TensorRT-10.0.1.6').is_dir(), 'TensorRT directory missing')
    require(command('dpkg-query', '-W', '-f=${Version}', 'mlnx-ofed-kernel-dkms').stdout == '24.01.OFED.24.01.0.3.3.1-1', 'OFED package changed')


def apply():
    require(os.geteuid() == 0 and sys.stdin.isatty(), 'Run --apply from the authenticated root console')
    origin = entry.console_origin(os.ttyname(sys.stdin.fileno()))
    os.umask(0o077)
    print('Checking the frozen bundle, console-entry backup, and idle GPU state...', flush=True)
    entry.check()
    quiet_services()
    no_gpu_clients()
    require(not PREFERENCE.exists() and not PREFERENCE.is_symlink(), 'Custom preference file already exists')
    evidence, backup = select_evidence()
    status_path = evidence / 'apply-status.json'
    require(not status_path.exists() and not status_path.is_symlink(), 'An apply attempt already exists; review its status before retrying')
    locked = json.loads((BUNDLE / 'package-lock.json').read_text())['target_packages']
    state = {'stage': 'preparing_offline_install', 'evidence': str(evidence), 'console_origin': origin,
             'source_driver': '535.183.06', 'target_driver': TARGET, 'reboot_performed': False,
             'backup_sha256': backup['backup_sha256']}

    def save(stage=None, **values):
        if stage:
            state['stage'] = stage
        state.update(values)
        pending = evidence / '.apply-status.pending'
        pending.write_text(json.dumps(state, indent=2) + '\n')
        pending.chmod(0o644)
        pending.replace(status_path)

    save()
    try:
        for script in (Path(__file__).resolve(), Path(entry.__file__).resolve()):
            shutil.copyfile(script, evidence / script.name)
        argv, debs, env = prepare_apt(evidence / 'offline-apt-610', locked)
        simulate(argv, debs, env, locked, evidence / 'install-before-uninstall.simulate.txt')
        quiet_services()
        no_gpu_clients()
        save('unloading_535')
        print('Offline simulation matched all 22 packages. Unloading the old NVIDIA modules...', flush=True)
        for module in MODULES:
            if module in loaded_modules():
                command('modprobe', '-r', module)
        require(not set(MODULES) & loaded_modules(), 'Old NVIDIA module remains loaded')
        quiet_services()
        save('uninstalling_535')
        print('Starting the old NVIDIA uninstaller. Confirm removal when it asks.', flush=True)
        installer_log = evidence / 'nvidia-uninstall-535.183.06.log'
        result = subprocess.run(['/usr/bin/nvidia-uninstall', '--ui=none',
                                 '--log-file-name=' + str(installer_log)], env=ENV)
        require(result.returncode == 0, 'NVIDIA uninstaller did not complete successfully')
        require(installer_log.is_file() and installer_log.stat().st_size > 0, 'Uninstaller evidence log missing')
        for old in ('/usr/bin/nvidia-uninstall', '/usr/bin/nvidia-installer',
                    '/usr/src/nvidia-535.183.06', '/var/lib/dkms/nvidia/535.183.06'):
            path = Path(old)
            require(not path.exists() and not path.is_symlink(), 'Old runfile path remains: ' + old)
        old_module_tree = Path('/lib/modules') / KERNEL / 'updates/dkms'
        for module in MODULES:
            require(not list(old_module_tree.glob(module.replace('_', '-') + '.ko*')),
                    'Old module file remains: ' + module)
        after_uninstall = command('dkms', 'status')
        previous = json.loads((evidence / 'baseline.json').read_text())['dkms_status']
        require(not after_uninstall.stderr.strip() and set(after_uninstall.stdout.splitlines()) ==
                {row for row in previous.splitlines() if not row.startswith('nvidia/')},
                'Post-uninstall DKMS state differs from the preserved OFED registrations')
        require(not set(MODULES) & loaded_modules(), 'NVIDIA module loaded during uninstall')
        require(str(Path('/usr/local/cuda').resolve()) == '/usr/local/cuda-12.2', 'CUDA default changed during uninstall')
        save('535_removed')
        quiet_services()
        simulate(argv, debs, env, locked, evidence / 'install-after-uninstall.simulate.txt')
        command('systemctl', 'mask', '--runtime', 'nvidia-persistenced.service')
        require(Path('/run/systemd/system/nvidia-persistenced.service').resolve() == Path('/dev/null'), 'Runtime mask failed')
        save('installing_610')
        print('Installing the 22 frozen packages and building the driver modules...', flush=True)
        install(argv, debs, env, evidence / 'install-610.log')
        with PREFERENCE.open('x') as output:
            output.write((BUNDLE / 'nvenc-driver-610-exact.pref').read_text())
        PREFERENCE.chmod(0o644)
        for preference in (PREFERENCE, Path('/etc/apt/preferences.d/nvidia-driver-pin')):
            require(preference.is_file(), 'Expected installed driver preference missing: ' + str(preference))
            shutil.copyfile(preference, evidence / preference.name)
        save('checking_before_reboot')
        installed_checks(evidence, locked)
        save('ready_for_reboot', installed_driver=TARGET, runtime_acceptance_pending=True)
        print(f'INSTALL CHECKS PASSED — READY FOR REBOOT REVIEW\nEvidence: {evidence}\nReturn to Codex with Ctrl+Alt+F3 before rebooting.', flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as exc:
        message = str(exc) or 'Interrupted by operator; inspect package and driver state before continuing'
        save(error=message, stopped=True)
        print(f'STOPPED during {state["stage"]}: {message}\nEvidence: {evidence}\nReturn to Codex with Ctrl+Alt+F3. Leave the machine running for review.', file=sys.stderr)
        return 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', required=True, action='store_true')
    parser.parse_args()
    try:
        return apply()
    except Exception as exc:
        print(f'STOPPED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
