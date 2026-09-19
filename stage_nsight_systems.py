#!/usr/bin/env python3
"""Stage a pinned official Nsight Systems archive without a system install.

The pin comes from NVIDIA's CUDA 13.1.1 redistribution manifest. To maintain
this script for a newer release, update the manifest URL/SHA and the component
version/path/SHA/size together from that release's official manifest, then
stage into a new prefix. The script never invokes a package manager, sudo,
ldconfig, alternatives, or a global PATH update.
"""
import argparse
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import posixpath
import tarfile
import tempfile

from stage_cuda_probe_toolkit import (
    NVIDIA_REDIST_PREFIX,
    audit_symlinks,
    checked_download,
    require_regular_file,
    sha256,
)


CUDA_RELEASE = '13.1.1'
NSIGHT_VERSION = '2025.5.2.266'
PLATFORM = 'linux-x86_64'
MANIFEST_URL = f'{NVIDIA_REDIST_PREFIX}redistrib_{CUDA_RELEASE}.json'
MANIFEST_SHA256 = '97cf605ccc4751825b1865f4af571c9b50dd29ffd13e9a38b296a9ecb1f0d422'
RELATIVE_PATH = (
    'nsight_systems/linux-x86_64/'
    'nsight_systems-linux-x86_64-2025.5.2.266-archive.tar.xz'
)
ARCHIVE_URL = NVIDIA_REDIST_PREFIX + RELATIVE_PATH
ARCHIVE_SHA256 = '76113b3f75ba620d11242a5b720c9eddd168be41ce879a5fb9d4f198181e19a4'
ARCHIVE_SIZE = 1098114048
ARCHIVE_NAME = Path(RELATIVE_PATH).name
ARCHIVE_ROOT = ARCHIVE_NAME.removesuffix('.tar.xz')


def validate_manifest(path):
    require_regular_file(path, 'NVIDIA redistribution manifest')
    if sha256(path) != MANIFEST_SHA256:
        raise ValueError(f'Manifest SHA256 mismatch: {path}')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('release_label') != CUDA_RELEASE:
        raise ValueError('NVIDIA manifest release mismatch')
    try:
        actual = manifest['nsight_systems'][PLATFORM]
    except (KeyError, TypeError) as exc:
        raise ValueError(f'Manifest lacks nsight_systems for {PLATFORM}') from exc
    expected = {
        'relative_path': RELATIVE_PATH,
        'sha256': ARCHIVE_SHA256,
        'size': str(ARCHIVE_SIZE),
    }
    for field, value in expected.items():
        if str(actual.get(field)) != value:
            raise ValueError(f'Manifest mismatch: nsight_systems/{field}')


def source_record():
    return {
        'cuda_redistribution_release': CUDA_RELEASE,
        'nsight_systems_version': NSIGHT_VERSION,
        'platform': PLATFORM,
        'manifest_url': MANIFEST_URL,
        'manifest_sha256': MANIFEST_SHA256,
        'archive_url': ARCHIVE_URL,
        'archive_relative_path': RELATIVE_PATH,
        'archive_sha256': ARCHIVE_SHA256,
        'archive_size': ARCHIVE_SIZE,
    }


def find_executable(root, name):
    matches = sorted(
        path for path in root.rglob(name)
        if path.is_file() and os.access(path, os.X_OK)
    )
    return str(matches[0]) if matches else None


def validate_archive_members(archive):
    entries = []
    seen = set()
    symlinks = set()
    root = PurePosixPath(ARCHIVE_ROOT)
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or '..' in path.parts or not path.parts:
            raise ValueError(f'Unsafe archive member path: {member.name}')
        if path.parts[0] != ARCHIVE_ROOT:
            raise ValueError(f'Archive member is outside expected root: {member.name}')
        if path in seen:
            raise ValueError(f'Duplicate archive member path: {member.name}')
        seen.add(path)
        if member.isdev() or member.isfifo():
            raise ValueError(f'Unsupported special archive member: {member.name}')
        if member.issym():
            target = PurePosixPath(member.linkname)
            if target.is_absolute():
                raise ValueError(f'Absolute archive symlink: {member.name}')
            resolved = PurePosixPath(posixpath.normpath(str(path.parent / target)))
            if resolved != root and root not in resolved.parents:
                raise ValueError(f'Escaping archive symlink: {member.name}')
            symlinks.add(path)
        elif member.islnk():
            target = PurePosixPath(member.linkname)
            if target.is_absolute():
                raise ValueError(f'Absolute archive hard link: {member.name}')
            resolved = PurePosixPath(posixpath.normpath(str(target)))
            if resolved != root and root not in resolved.parents:
                raise ValueError(f'Escaping archive hard link: {member.name}')
        entries.append(path)

    for path in entries:
        if any(parent in symlinks for parent in path.parents):
            raise ValueError(f'Archive member traverses a symlink: {path}')


def stage(manifest_cache, archive_cache, prefix):
    if os.geteuid() == 0:
        raise ValueError('Run as the normal user; this tool never needs root')
    if prefix.exists() or prefix.is_symlink():
        raise ValueError(f'Prefix must be new: {prefix}')

    manifest_cache.parent.mkdir(parents=True, exist_ok=True)
    archive_cache.parent.mkdir(parents=True, exist_ok=True)
    checked_download(MANIFEST_URL, manifest_cache, MANIFEST_SHA256)
    validate_manifest(manifest_cache)
    checked_download(ARCHIVE_URL, archive_cache, ARCHIVE_SHA256, ARCHIVE_SIZE)

    prefix.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix='.nsight-systems-stage-', dir=prefix.parent) as temporary:
        temporary = Path(temporary)
        extracted = temporary / 'extracted'
        extracted.mkdir()
        with tarfile.open(archive_cache) as archive:
            validate_archive_members(archive)
            archive.extractall(extracted, filter='tar')
        roots = list(extracted.iterdir())
        if len(roots) != 1 or not roots[0].is_dir() or roots[0].is_symlink():
            raise ValueError(f'Unexpected archive layout: {archive_cache}')

        staged = roots[0]
        audit_symlinks(staged)
        (staged / 'pancakebatter-nsight-systems-source.json').write_text(
            json.dumps(source_record(), indent=2) + '\n', encoding='utf-8')
        (staged / 'README.pancakebatter.txt').write_text(
            f'Official NVIDIA Nsight Systems {NSIGHT_VERSION}, staged locally.\n'
            'No system package, alternatives, ldconfig, or PATH was changed.\n'
            f'Recreate with: {Path(__file__).resolve()} '
            f'--manifest-cache {manifest_cache} --archive-cache {archive_cache} '
            f'--prefix {prefix}\n',
            encoding='utf-8')
        staged.rename(prefix)

    return {
        **source_record(),
        'prefix': str(prefix),
        'nsys': find_executable(prefix, 'nsys'),
        'qdstrm_importer': find_executable(prefix, 'QdstrmImporter'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--manifest-cache', type=Path,
        default=Path('/tmp') / f'cuda-redistrib-{CUDA_RELEASE}.json')
    parser.add_argument(
        '--archive-cache', type=Path,
        default=Path('/tmp') / ARCHIVE_NAME)
    parser.add_argument(
        '--prefix', type=Path,
        default=Path.home() / '.local/opt/nsight-systems-2025.5.2-nvenc')
    args = parser.parse_args()
    result = stage(
        args.manifest_cache.absolute(), args.archive_cache.absolute(),
        args.prefix.absolute())
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
