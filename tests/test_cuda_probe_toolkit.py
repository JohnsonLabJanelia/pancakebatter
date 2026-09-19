import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage_cuda_probe_toolkit as toolkit


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()


class DownloadCacheTests(unittest.TestCase):
    def test_cached_symlink_is_rejected_without_network_access(self):
        payload = b'pinned archive'
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / 'target'
            target.write_bytes(payload)
            destination = root / 'archive.tar.xz'
            destination.symlink_to(target)
            with mock.patch.object(toolkit.urllib.request, 'urlopen') as urlopen:
                with self.assertRaisesRegex(ValueError, 'regular file'):
                    toolkit.checked_download(
                        toolkit.NVIDIA_REDIST_PREFIX + 'archive.tar.xz',
                        destination, digest, len(payload))
                urlopen.assert_not_called()

    def test_tampered_cached_file_is_rejected_without_redownload(self):
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / 'archive.tar.xz'
            destination.write_bytes(b'tampered')
            digest = hashlib.sha256(b'expected').hexdigest()
            with mock.patch.object(toolkit.urllib.request, 'urlopen') as urlopen:
                with self.assertRaisesRegex(ValueError, 'SHA256 mismatch'):
                    toolkit.checked_download(
                        toolkit.NVIDIA_REDIST_PREFIX + 'archive.tar.xz',
                        destination, digest)
                urlopen.assert_not_called()

    def test_download_uses_unique_temporary_without_clobbering_legacy_name(self):
        payload = b'new archive bytes'
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / 'archive.tar.xz'
            legacy_partial = root / 'archive.tar.xz.partial'
            legacy_partial.write_bytes(b'do not replace')
            with mock.patch.object(
                    toolkit.urllib.request, 'urlopen',
                    return_value=Response(payload)):
                toolkit.checked_download(
                    toolkit.NVIDIA_REDIST_PREFIX + 'archive.tar.xz',
                    destination, digest, len(payload))
            self.assertEqual(destination.read_bytes(), payload)
            self.assertEqual(legacy_partial.read_bytes(), b'do not replace')
            self.assertEqual(list(root.glob('.archive.tar.xz.*.partial')), [])


class MergeSafetyTests(unittest.TestCase):
    def test_archive_symlink_that_escapes_component_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            output = root / 'output'
            (source / 'lib').mkdir(parents=True)
            output.mkdir()
            (source / 'lib' / 'escape').symlink_to('../../outside')
            with self.assertRaisesRegex(ValueError, 'Escaping archive symlink'):
                toolkit.merge_component(source, output, 'component')

    def test_internal_relative_symlink_is_preserved_and_audited(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            output = root / 'output'
            (source / 'lib').mkdir(parents=True)
            output.mkdir()
            (source / 'lib' / 'libexample.so.1').write_bytes(b'library')
            (source / 'lib' / 'libexample.so').symlink_to('libexample.so.1')
            toolkit.merge_component(source, output, 'component')
            toolkit.audit_symlinks(output)
            self.assertEqual(os.readlink(output / 'lib' / 'libexample.so'),
                             'libexample.so.1')

    def test_final_audit_catches_symlink_chain_that_escapes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'output'
            (output / 'sub').mkdir(parents=True)
            (output / 'first').symlink_to('sub/second')
            (output / 'sub' / 'second').symlink_to('../../outside')
            with self.assertRaisesRegex(ValueError, 'Escaping staged symlink'):
                toolkit.audit_symlinks(output)

    def test_regular_file_cannot_replace_destination_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            output = root / 'output'
            source.mkdir()
            output.mkdir()
            (source / 'item').write_bytes(b'archive data')
            outside = root / 'outside'
            outside.write_bytes(b'keep')
            (output / 'item').symlink_to(outside)
            with self.assertRaisesRegex(ValueError, 'Conflicting file'):
                toolkit.merge_component(source, output, 'component')
            self.assertEqual(outside.read_bytes(), b'keep')

    def test_different_regular_files_at_same_path_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / 'first'
            second = root / 'second'
            output = root / 'output'
            first.mkdir()
            second.mkdir()
            output.mkdir()
            (first / 'item').write_bytes(b'first')
            (second / 'item').write_bytes(b'second')
            toolkit.merge_component(first, output, 'first')
            with self.assertRaisesRegex(ValueError, 'Conflicting file'):
                toolkit.merge_component(second, output, 'second')


class PkgConfigTests(unittest.TestCase):
    @staticmethod
    def upstream_text(package, description, library):
        return (
            'cudaroot=/usr/local/cuda-13.1\n'
            'libdir=${cudaroot}/targets/x86_64-linux/lib\n'
            'includedir=${cudaroot}/targets/x86_64-linux/include\n'
            '\n'
            f'Name: {package}\n'
            f'Description: {description}\n'
            'Version: 13.1\n'
            f'Libs: -L${{libdir}} -l{library}\n'
            'Cflags: -I${includedir}\n'
        )

    def make_upstream_files(self, root):
        directory = root / 'pkg-config'
        directory.mkdir()
        (directory / 'cuda-13.1.pc').write_text(
            self.upstream_text('cuda', 'CUDA Driver Library', 'cuda'),
            encoding='utf-8')
        (directory / 'cudart-13.1.pc').write_text(
            self.upstream_text('cudart', 'CUDA Runtime Library', 'cudart'),
            encoding='utf-8')
        return directory

    def test_relocated_files_resolve_to_flat_prefix(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = self.make_upstream_files(root)
            toolkit.relocate_pkg_config_files(root, '13.1.1')
            for path in directory.glob('*.pc'):
                text = path.read_text(encoding='utf-8')
                self.assertIn('prefix=${pcfiledir}/..\n', text)
                self.assertIn('libdir=${prefix}/lib\n', text)
                self.assertIn('includedir=${prefix}/include\n', text)
                self.assertNotIn('/usr/local/cuda', text)

            pkg_config = shutil.which('pkg-config')
            if pkg_config is None:
                self.skipTest('pkg-config is unavailable')
            environment = os.environ.copy()
            environment['PKG_CONFIG_PATH'] = str(directory)
            environment['PKG_CONFIG_LIBDIR'] = str(directory)
            for variable, expected in (
                    ('includedir', root / 'include'), ('libdir', root / 'lib')):
                value = subprocess.check_output(
                    [pkg_config, f'--variable={variable}', 'cudart-13.1'],
                    env=environment, text=True).strip()
                self.assertEqual(Path(value).resolve(), expected.resolve())

    def test_unexpected_upstream_content_is_rejected_before_rewrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = self.make_upstream_files(root)
            cuda_path = directory / 'cuda-13.1.pc'
            original_cuda = cuda_path.read_text(encoding='utf-8')
            cudart_path = directory / 'cudart-13.1.pc'
            cudart_path.write_text(
                cudart_path.read_text(encoding='utf-8').replace(
                    '/usr/local/cuda-13.1', '/unexpected'),
                encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'Unexpected upstream'):
                toolkit.relocate_pkg_config_files(root, '13.1.1')
            self.assertEqual(cuda_path.read_text(encoding='utf-8'), original_cuda)


class LockMetadataTests(unittest.TestCase):
    @staticmethod
    def valid_lock():
        return {
            'release': '13.1.1',
            'platform': 'linux-x86_64',
            'manifest_url': (
                toolkit.NVIDIA_REDIST_PREFIX + 'redistrib_13.1.1.json'),
            'base_url': toolkit.NVIDIA_REDIST_PREFIX,
            'components': {
                'cuda_nvcc': {
                    'version': '13.1.115',
                    'relative_path': (
                        'cuda_nvcc/linux-x86_64/'
                        'cuda_nvcc-linux-x86_64-13.1.115-archive.tar.xz'),
                },
            },
        }

    def test_unsupported_platform_is_rejected(self):
        lock = self.valid_lock()
        lock['platform'] = 'linux-aarch64'
        with self.assertRaisesRegex(ValueError, 'Unsupported lock platform'):
            toolkit.validate_lock_metadata(lock)

    def test_version_must_match_archive_path(self):
        lock = self.valid_lock()
        lock['components']['cuda_nvcc']['version'] = '13.1.999'
        with self.assertRaisesRegex(ValueError, 'version/path mismatch'):
            toolkit.validate_lock_metadata(lock)


if __name__ == '__main__':
    unittest.main()
