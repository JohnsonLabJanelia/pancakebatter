import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nvidia_driver_enter_console as entry


class ConsoleEntryGuards(unittest.TestCase):
    def test_nonroot_refused_before_checks(self):
        with patch.object(entry.os, 'geteuid', return_value=1000), patch.object(entry, 'check') as check:
            with self.assertRaisesRegex(RuntimeError, 'root console'):
                entry.enter()
            check.assert_not_called()

    def test_desktop_pty_refused_before_checks(self):
        with patch.object(entry.os, 'geteuid', return_value=0), \
             patch.object(entry.sys, 'stdin', Mock(isatty=lambda: True, fileno=lambda: 0)), \
             patch.object(entry.os, 'ttyname', return_value='/dev/pts/10'), \
             patch.object(entry, 'process_identity', return_value={
                 'pid': 100, 'ppid': 1, 'comm': 'bash', 'euid': 0, 'console': None}), \
             patch.object(entry, 'check') as check:
            with self.assertRaisesRegex(RuntimeError, 'Linux VT'):
                entry.enter()
            check.assert_not_called()

    def test_sudo_pty_accepts_root_login_on_vt(self):
        rows = {
            105: {'ppid': 104, 'comm': 'python3', 'euid': 0, 'console': None},
            104: {'ppid': 103, 'comm': 'bash', 'euid': 0, 'console': None},
            103: {'ppid': 102, 'comm': 'sudo', 'euid': 0, 'console': None},
            102: {'ppid': 101, 'comm': 'sudo', 'euid': 0, 'console': '/dev/tty4'},
            101: {'ppid': 100, 'comm': 'bash', 'euid': 1000, 'console': '/dev/tty4'},
            100: {'ppid': 1, 'comm': 'login', 'euid': 0, 'console': '/dev/tty4'},
        }
        with patch.object(entry, 'process_identity', side_effect=rows.__getitem__):
            result = entry.console_origin('/dev/pts/4', pid=105)
        self.assertEqual(result['console'], '/dev/tty4')
        self.assertTrue(result['via_sudo_pty'])

    def test_sudo_pty_rejects_desktop_or_ssh_even_with_sudo_tty_env(self):
        for parent in ('gnome-terminal-', 'sshd'):
            with self.subTest(parent=parent), patch.dict(os.environ, {'SUDO_TTY': '/dev/tty4'}), \
                 patch.object(entry, 'process_identity', side_effect=[
                     {'ppid': 99, 'comm': 'sudo', 'euid': 0, 'console': None},
                     {'ppid': 1, 'comm': parent, 'euid': 0, 'console': None},
                 ]):
                with self.assertRaisesRegex(RuntimeError, 'Linux VT'):
                    entry.console_origin('/dev/pts/4', pid=100)

    def test_sudo_pty_rejects_missing_or_inconsistent_ancestry(self):
        with patch.object(entry, 'process_identity', side_effect=FileNotFoundError('process exited')):
            with self.assertRaisesRegex(RuntimeError, 'verify Linux VT'):
                entry.console_origin('/dev/pts/4', pid=100)
        with patch.object(entry, 'process_identity', side_effect=[
            {'ppid': 99, 'comm': 'sudo', 'euid': 0, 'console': '/dev/tty4'},
            {'ppid': 1, 'comm': 'login', 'euid': 0, 'console': '/dev/tty5'},
        ]):
            with self.assertRaisesRegex(RuntimeError, 'ancestry does not match'):
                entry.console_origin('/dev/pts/4', pid=100)

    def test_truncated_installer_process_is_busy(self):
        for name in ('nvidia-installe', 'nvidia-uninstal'):
            with self.subTest(name=name), patch.object(entry, 'run', return_value=Mock(stdout=name + '\n')):
                with self.assertRaisesRegex(RuntimeError, 'already running'):
                    entry.package_manager_idle()

    def blocked_before_desktop(self, scenario):
        with tempfile.TemporaryDirectory() as temporary:
            evidence = Path(temporary) / 'evidence'
            evidence.mkdir()
            commands = []

            def fake_run(*argv, **kwargs):
                commands.append(argv)
                return subprocess.CompletedProcess(argv, 0, '100\ttotal\n' if argv[0] == 'du' else '', '')

            def fake_tar(argv, **kwargs):
                if '--create' in argv:
                    Path(argv[argv.index('--file') + 1]).write_bytes(b'backup fixture')
                return subprocess.CompletedProcess(argv, int('--compare' in argv and scenario == 'backup'))

            previous_mask = os.umask(0o022)
            try:
                with contextlib.ExitStack() as stack:
                    for target, name, kwargs in (
                        (entry.os, 'geteuid', {'return_value': 0}),
                        (entry.sys, 'stdin', {'new': Mock(isatty=lambda: True, fileno=lambda: 0)}),
                        (entry.os, 'ttyname', {'return_value': '/dev/tty4'}),
                        (entry, 'check', {'return_value': {'checks_passed': True}}),
                        (entry.tempfile, 'mkdtemp', {'return_value': str(evidence)}),
                        (entry, 'service_state', {'return_value': 'inactive'}),
                        (entry, 'package_manager_idle', {}),
                        (entry, 'package_locks_unused', {}),
                        (entry, 'run', {'side_effect': fake_run}),
                        (entry.subprocess, 'run', {'side_effect': fake_tar}),
                        (entry.shutil, 'copyfile', {}),
                        (entry, 'compute_idle', {'side_effect': RuntimeError('compute busy')}),
                    ):
                        stack.enter_context(patch.object(target, name, **kwargs))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    stack.enter_context(contextlib.redirect_stderr(io.StringIO()))
                    self.assertEqual(entry.enter(), 2)
                self.assertNotIn(('systemctl', 'stop', 'gdm3.service'), commands)
                status = json.loads((evidence / 'status.json').read_text())
                self.assertEqual(status['stage'], 'stopped_for_review')
                self.assertEqual(status['backup_verified'], scenario == 'compute')
                self.assertEqual(status['service_states']['gdm3.service'], 'inactive')
                self.assertEqual((evidence / 'driver-state-before.tar').stat().st_mode & 0o777, 0o600)
                self.assertEqual((evidence / 'status.json').stat().st_mode & 0o777, 0o644)
                self.assertEqual(evidence.stat().st_mode & 0o777, 0o711)
            finally:
                os.umask(previous_mask)

    def test_failed_backup_comparison_prevents_desktop_stop(self):
        self.blocked_before_desktop('backup')

    def test_compute_appearing_after_backup_prevents_desktop_stop(self):
        self.blocked_before_desktop('compute')


if __name__ == '__main__':
    unittest.main()
