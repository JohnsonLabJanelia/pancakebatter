#!/usr/bin/env python3
"""Stage pinned NVIDIA compiler/runtime archives in a new caller-selected prefix.

This is a minimal toolkit for the standalone NVENC experiment, not a system
CUDA installer. It never updates the driver, alternatives, ldconfig or PATH.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tarfile
import tempfile
import urllib.request


NVIDIA_REDIST_PREFIX = 'https://developer.download.nvidia.com/compute/cuda/redist/'
SUPPORTED_PLATFORM = 'linux-x86_64'
PKG_CONFIG_PACKAGES = {
    'cuda': ('CUDA Driver Library', 'cuda'),
    'cudart': ('CUDA Runtime Library', 'cudart'),
}


def sha256(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def require_regular_file(path, label):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError as exc:
        raise ValueError(f'Missing {label}: {path}') from exc
    if not stat.S_ISREG(mode):
        raise ValueError(f'{label} must be a regular file: {path}')


def verify_file(path, digest, size, label):
    require_regular_file(path, label)
    if size is not None and path.stat().st_size != int(size):
        raise ValueError(f'Size mismatch for {path}')
    if sha256(path) != digest:
        raise ValueError(f'SHA256 mismatch for {path}')


def checked_download(url, dest, digest, size=None):
    if not url.startswith(NVIDIA_REDIST_PREFIX):
        raise ValueError('Expected NVIDIA CUDA redistributable URL')
    try:
        dest.lstat()
    except FileNotFoundError:
        pass
    else:
        verify_file(dest, digest, size, 'cached download')
        return

    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
                mode='wb', prefix=f'.{dest.name}.', suffix='.partial',
                dir=dest.parent, delete=False) as output:
            temporary = Path(output.name)
            with urllib.request.urlopen(url, timeout=60) as source:
                shutil.copyfileobj(source, output)
        verify_file(temporary, digest, size, 'downloaded temporary file')

        # Publish without replacing a path created concurrently. Any concurrent
        # winner must independently pass the same regular-file and hash checks.
        try:
            os.link(temporary, dest)
        except FileExistsError:
            verify_file(dest, digest, size, 'concurrent cached download')
        else:
            verify_file(dest, digest, size, 'published cached download')
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def is_within(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_link_target(link_path, raw_target, root, label):
    target = Path(raw_target)
    if target.is_absolute():
        raise ValueError(f'Absolute {label}: {link_path} -> {raw_target}')
    try:
        resolved_root = root.resolve(strict=True)
        resolved_target = (link_path.parent / target).resolve(strict=False)
    except RuntimeError as exc:
        raise ValueError(f'Symlink loop in {label}: {link_path}') from exc
    if not is_within(resolved_target, resolved_root):
        raise ValueError(f'Escaping {label}: {link_path} -> {raw_target}')


def ensure_real_directory(path):
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        path.mkdir()
        return
    if not stat.S_ISDIR(mode):
        raise ValueError(f'Path collision at directory: {path}')


def merge_regular_file(source, dest):
    try:
        mode = dest.lstat().st_mode
    except FileNotFoundError:
        shutil.copy2(source, dest)
        return
    if not stat.S_ISREG(mode) or sha256(dest) != sha256(source):
        raise ValueError(f'Conflicting file: {dest}')


def audit_symlinks(root):
    for current, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            path = Path(current) / name
            if path.is_symlink():
                validate_link_target(path, os.readlink(path), root, 'staged symlink')


def merge_component(source, output, component, source_root=None, output_root=None):
    source_root = source if source_root is None else source_root
    output_root = output if output_root is None else output_root
    for item in sorted(source.iterdir()):
        if item.name.startswith('LICENSE') and item.is_file() and not item.is_symlink():
            licenses = output_root / 'licenses'
            ensure_real_directory(licenses)
            license_dir = licenses / component
            ensure_real_directory(license_dir)
            merge_regular_file(item, license_dir / item.name)
            continue
        dest = output / item.name
        if item.is_symlink():
            link = os.readlink(item)
            validate_link_target(item, link, source_root, 'archive symlink')
            validate_link_target(dest, link, output_root, 'staged symlink')
            try:
                mode = dest.lstat().st_mode
            except FileNotFoundError:
                dest.symlink_to(link)
            else:
                if not stat.S_ISLNK(mode) or os.readlink(dest) != link:
                    raise ValueError(f'Conflicting symlink: {dest}')
        elif item.is_dir():
            ensure_real_directory(dest)
            merge_component(item, dest, component,
                            source_root=source_root, output_root=output_root)
        elif item.is_file():
            merge_regular_file(item, dest)
        else:
            raise ValueError(f'Unsupported archive item: {item}')


def release_series(release):
    if not isinstance(release, str):
        raise ValueError(f'Invalid CUDA release: {release!r}')
    parts = release.split('.')
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(f'Invalid CUDA release: {release!r}')
    return '.'.join(parts[:2])


def pkg_config_text(package, series, *, relocatable):
    description, library = PKG_CONFIG_PACKAGES[package]
    if relocatable:
        paths = (
            'prefix=${pcfiledir}/..\n'
            'libdir=${prefix}/lib\n'
            'includedir=${prefix}/include\n'
        )
    else:
        paths = (
            f'cudaroot=/usr/local/cuda-{series}\n'
            'libdir=${cudaroot}/targets/x86_64-linux/lib\n'
            'includedir=${cudaroot}/targets/x86_64-linux/include\n'
        )
    return (
        f'{paths}\n'
        f'Name: {package}\n'
        f'Description: {description}\n'
        f'Version: {series}\n'
        f'Libs: -L${{libdir}} -l{library}\n'
        'Cflags: -I${includedir}\n'
    )


def relocate_pkg_config_files(output, release):
    series = release_series(release)
    directory = output / 'pkg-config'
    validated = []
    for package in PKG_CONFIG_PACKAGES:
        path = directory / f'{package}-{series}.pc'
        require_regular_file(path, 'CUDA pkg-config file')
        expected = pkg_config_text(package, series, relocatable=False)
        if path.read_text(encoding='utf-8') != expected:
            raise ValueError(f'Unexpected upstream pkg-config content: {path}')
        validated.append((package, path))
    for package, path in validated:
        path.write_text(pkg_config_text(package, series, relocatable=True),
                        encoding='utf-8')


def validate_lock_metadata(lock):
    release = lock.get('release')
    release_series(release)
    platform = lock.get('platform')
    if platform != SUPPORTED_PLATFORM:
        raise ValueError(
            f'Unsupported lock platform {platform!r}; expected {SUPPORTED_PLATFORM!r}')
    if lock.get('base_url') != NVIDIA_REDIST_PREFIX:
        raise ValueError('Lock base_url is not the NVIDIA redistribution root')
    expected_manifest_url = f'{NVIDIA_REDIST_PREFIX}redistrib_{release}.json'
    if lock.get('manifest_url') != expected_manifest_url:
        raise ValueError('Lock manifest URL does not match its CUDA release')
    components = lock.get('components')
    if not isinstance(components, dict) or not components:
        raise ValueError('Lock must contain at least one component')
    for name, pinned in components.items():
        version = pinned.get('version')
        if not isinstance(version, str) or not version:
            raise ValueError(f'Missing pinned version for {name}')
        archive = f'{name}-{platform}-{version}-archive.tar.xz'
        expected_path = f'{name}/{platform}/{archive}'
        if pinned.get('relative_path') != expected_path:
            raise ValueError(
                f'Pinned version/path mismatch for {name}: expected {expected_path}')
    return release, platform, components


def stage(lock_path, prefix, cache):
    if os.geteuid() == 0:
        raise ValueError('Run as the normal user; this tool never needs root')
    if prefix.exists() or prefix.is_symlink():
        raise ValueError(f'Prefix must be new: {prefix}')
    lock = json.loads(lock_path.read_text(encoding='utf-8'))
    release, platform, components = validate_lock_metadata(lock)
    cache.mkdir(parents=True, exist_ok=True)
    manifest = cache / f'redistrib_{release}.json'
    checked_download(lock['manifest_url'], manifest, lock['manifest_sha256'])
    upstream = json.loads(manifest.read_text(encoding='utf-8'))
    if upstream.get('release_label') != release:
        raise ValueError('Manifest release mismatch')
    archives = []
    for name, pinned in components.items():
        try:
            actual = upstream[name][platform]
        except (KeyError, TypeError) as exc:
            raise ValueError(f'Manifest lacks {name} for {platform}') from exc
        for field in ('relative_path', 'sha256', 'size'):
            if str(pinned[field]) != str(actual[field]):
                raise ValueError(f'Manifest mismatch: {name}/{field}')
        archive = cache / Path(pinned['relative_path']).name
        checked_download(lock['base_url'] + pinned['relative_path'], archive,
                         pinned['sha256'], int(pinned['size']))
        archives.append((name, archive))
    prefix.parent.mkdir(parents=True, exist_ok=True)
    # A failed extraction never creates the final prefix.
    with tempfile.TemporaryDirectory(prefix='.cuda-probe-stage-', dir=prefix.parent) as temp:
        temp = Path(temp)
        output = temp / 'toolkit'
        output.mkdir()
        for name, archive in archives:
            extracted = temp / name
            extracted.mkdir()
            with tarfile.open(archive) as tf:
                tf.extractall(extracted, filter='data')
            roots = list(extracted.iterdir())
            if len(roots) != 1 or not roots[0].is_dir():
                raise ValueError(f'Unexpected archive layout: {archive}')
            if roots[0].is_symlink():
                raise ValueError(f'Archive root must be a real directory: {archive}')
            merge_component(roots[0], output, name)
        relocate_pkg_config_files(output, release)
        lib64 = output / 'lib64'
        try:
            lib64_mode = lib64.lstat().st_mode
        except FileNotFoundError:
            lib64.symlink_to('lib')
        else:
            if not stat.S_ISLNK(lib64_mode) or os.readlink(lib64) != 'lib':
                raise ValueError(f'Conflicting compatibility symlink: {lib64}')
        audit_symlinks(output)
        shutil.copy2(lock_path, output / 'pancakebatter-toolkit-lock.json')
        shutil.copy2(manifest, output / 'nvidia-redistrib-manifest.json')
        (output / 'README.pancakebatter.txt').write_text(
            f'CUDA {release} compiler/runtime subset for the NVENC probe.\n'
            'This is a caller-selected, new user-writable destination; it may be ephemeral.\n'
            'Use explicit bin/include/lib paths. No global configuration was changed.\n'
            'Driver and system libcuda are supplied by the separately installed driver.\n'
            'This prefix intentionally omits math libraries and profilers.\n',
            encoding='utf-8')
        output.rename(prefix)
    return {'prefix':str(prefix), 'release':release, 'components':list(components),
            'production_cuda':str(Path('/usr/local/cuda').resolve())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=Path(__file__).parent / 'configs/cuda_probe_toolkit_13_1_1.json')
    parser.add_argument('--prefix', type=Path, required=True,
                        help='new caller-selected user-writable destination')
    parser.add_argument('--cache', type=Path, required=True,
                        help='caller-selected user-writable download cache')
    args = parser.parse_args()
    print(json.dumps(stage(args.lock.resolve(), args.prefix.absolute(), args.cache.absolute()), indent=2))


if __name__ == '__main__':
    main()
