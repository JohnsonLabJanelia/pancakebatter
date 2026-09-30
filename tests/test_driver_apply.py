import json
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nvidia_driver_apply as apply


class FrozenTransactionTests(unittest.TestCase):
    def setUp(self):
        self.locked = [{'package': f'p{i}', 'version': '1.2.3', 'architecture': 'amd64'} for i in range(22)]
        self.lines = [f'{action} p{i} (1.2.3 local-deb [amd64])'
                      for action in ('Inst', 'Conf') for i in range(22)]

    def test_exact_transaction_accepted(self):
        apply.verify_simulation('\n'.join(self.lines), self.locked)

    def test_removal_or_purge_refused(self):
        for prefix in ('Remv', 'Purg'):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(RuntimeError, 'removal'):
                apply.verify_simulation('\n'.join(self.lines + [f'{prefix} mlnx-ofed-kernel-dkms [old]']), self.locked)

    def test_incomplete_or_additional_configuration_refused(self):
        for changed in (self.lines[:-1], self.lines + ['Conf other (2 local-deb [amd64])']):
            with self.assertRaisesRegex(RuntimeError, 'configure set'):
                apply.verify_simulation('\n'.join(changed), self.locked)

    def test_wrong_version_or_architecture_refused(self):
        for replacement in ('Inst p0 (9 local-deb [amd64])', 'Inst p0 (1.2.3 local-deb [i386])'):
            with self.subTest(replacement=replacement), self.assertRaisesRegex(RuntimeError, 'install set'):
                apply.verify_simulation('\n'.join([replacement, *self.lines[1:]]), self.locked)

    def test_duplicate_or_malformed_install_refused(self):
        for changed in ([*self.lines, self.lines[0]], ['Inst p0 truncated', *self.lines[1:]]):
            with self.assertRaises(RuntimeError):
                apply.verify_simulation('\n'.join(changed), self.locked)

    def test_upgrade_line_with_previous_version(self):
        lines = list(self.lines)
        lines[0] = 'Inst p0 [1.1] (1.2.3 local-deb [amd64])'
        apply.verify_simulation('\n'.join(lines), self.locked)


class ApplyBoundaryTests(unittest.TestCase):
    def test_nonroot_stops_before_preflight(self):
        with patch.object(apply.os, 'geteuid', return_value=1000), patch.object(apply.entry, 'check') as check:
            with self.assertRaisesRegex(RuntimeError, 'root console'):
                apply.apply()
            check.assert_not_called()

    def test_unverified_console_stops_before_preflight(self):
        with patch.object(apply.os, 'geteuid', return_value=0), \
             patch.object(apply.sys, 'stdin', Mock(isatty=lambda: True, fileno=lambda: 0)), \
             patch.object(apply.os, 'ttyname', return_value='/dev/pts/8'), \
             patch.object(apply.entry, 'console_origin', side_effect=RuntimeError('unverified console')), \
             patch.object(apply.entry, 'check') as check:
            with self.assertRaisesRegex(RuntimeError, 'unverified console'):
                apply.apply()
            check.assert_not_called()

    def test_gpu_clients_abort_before_removal(self):
        with patch.object(apply.entry, 'compute_idle', side_effect=RuntimeError('GPU busy')), \
             patch.object(apply, 'command') as command:
            with self.assertRaisesRegex(RuntimeError, 'GPU busy'):
                apply.no_gpu_clients()
            command.assert_not_called()

    def assert_uninstaller_blocked(self, failure):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'package-lock.json').write_text(json.dumps({'target_packages': []}))
            old_mask = os.umask(0o022)
            try:
                with contextlib.ExitStack() as stack:
                    mocks = {}
                    for target, name, kwargs in (
                        (apply.os, 'geteuid', {'return_value': 0}),
                        (apply.sys, 'stdin', {'new': Mock(isatty=lambda: True, fileno=lambda: 0)}),
                        (apply.os, 'ttyname', {'return_value': '/dev/tty4'}),
                        (apply.entry, 'console_origin', {'return_value': {'console': '/dev/tty4'}}),
                        (apply.entry, 'check', {}),
                        (apply, 'quiet_services', {}),
                        (apply, 'no_gpu_clients', {}),
                        (apply, 'PREFERENCE', {'new': root / 'absent-pref'}),
                        (apply, 'select_evidence', {'return_value': (root, {'backup_sha256': 'fixture'})}),
                        (apply, 'BUNDLE', {'new': root}),
                        (apply.shutil, 'copyfile', {}),
                        (apply, 'prepare_apt', {'return_value': ([], [], {})}),
                        (apply, 'simulate', {'side_effect': RuntimeError('simulation failed') if failure == 'simulate' else None}),
                        (apply, 'loaded_modules', {'return_value': set(apply.MODULES)}),
                        (apply, 'command', {'side_effect': RuntimeError('module busy')}),
                        (apply.subprocess, 'run', {}),
                    ):
                        mocks[name] = stack.enter_context(patch.object(target, name, **kwargs))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                    self.assertEqual(apply.apply(), 2)
                    mocks['run'].assert_not_called()
                    if failure == 'simulate':
                        mocks['command'].assert_not_called()
                status = json.loads((root / 'apply-status.json').read_text())
                self.assertTrue(status['stopped'])
                self.assertEqual(status['stage'], 'preparing_offline_install' if failure == 'simulate' else 'unloading_535')
            finally:
                os.umask(old_mask)

    def test_failed_simulation_prevents_module_unload_and_uninstall(self):
        self.assert_uninstaller_blocked('simulate')

    def test_failed_module_unload_prevents_uninstall(self):
        self.assert_uninstaller_blocked('unload')


if __name__ == '__main__':
    unittest.main()
