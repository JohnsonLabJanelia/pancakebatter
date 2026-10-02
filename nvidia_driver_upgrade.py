#!/usr/bin/env python3
"""Inspect the host and simulate packages; this tool has no installation mode."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from datetime import datetime, timezone

from hostconfig import host_file
from driver_upgrade.host import audit_host
from driver_upgrade.apt_plan import build_apt_plan

DEFAULT_MANIFEST = host_file("nvidia_driver_upgrade.json")
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def validate_manifest(manifest):
    """Reject malformed command/config inputs before issuing any subprocess."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError("manifest schema_version must be 1")
    for section in ("expected", "target", "recovery"):
        if not isinstance(manifest.get(section), dict):
            raise ValueError(f"manifest.{section} must be an object")
    expected = manifest["expected"]
    for key in ("os_id", "os_version_id", "architecture", "kernel", "driver_version",
                "driver_owner", "driver_flavor", "dkms_version", "ofed_dkms_version", "ofed_package_version"):
        value = expected.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.+:_-]*", value):
            raise ValueError(f"invalid expected.{key}")
    uuids = expected.get("gpu_uuids")
    if (not isinstance(uuids, list) or not uuids or
            any(not isinstance(x, str) or not re.fullmatch(r"GPU-[0-9a-f-]{36}", x) for x in uuids)
            or len(set(uuids)) != len(uuids)):
        raise ValueError("expected.gpu_uuids must contain unique GPU UUIDs")
    target = manifest["target"]
    for key in ("driver_version", "package_version", "dkms_package_version", "pin_package_version"):
        if not isinstance(target.get(key), str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+:~-]*", target[key]):
            raise ValueError(f"invalid target.{key}")
    for key in ("driver_metapackage", "pin_package"):
        if not isinstance(target.get(key), str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", target[key]):
            raise ValueError(f"invalid target.{key}")
    if target["driver_metapackage"] != "cuda-drivers" or target["module_flavor"] != "proprietary":
        raise ValueError("this plan supports cuda-drivers with proprietary modules only")
    allowed = target.get("allowed_dependency_packages", [])
    if not isinstance(allowed, list) or any(not isinstance(x, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", x) for x in allowed):
        raise ValueError("invalid target.allowed_dependency_packages")
    for key in ("dependency_package_versions", "framework_package_versions"):
        versions = target.get(key)
        if not isinstance(versions, dict) or any(
                not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9+.-]*", name)
                or not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.+:~-]*", version)
                for name, version in versions.items()):
            raise ValueError(f"invalid target.{key}")
    for section, keys in ((expected, ("cuda_symlink_target", "tensorrt_root")),
                          (manifest["recovery"], ("backup_image", "session_recovery", "rollback_installer"))):
        for key in keys:
            value = section.get(key)
            if not isinstance(value, str) or not value.startswith("/") or any(ord(x) < 32 for x in value):
                raise ValueError(f"{key} must be an absolute path without control characters")
    recovery = manifest["recovery"]
    for key in ("source_disk", "source_disk_serial", "backup_filesystem_uuid"):
        if not isinstance(recovery.get(key), str) or not re.fullmatch(r"[A-Za-z0-9-]+", recovery[key]):
            raise ValueError(f"invalid recovery.{key}")
    digest = recovery.get("rollback_installer_sha256")
    if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise ValueError("rollback_installer_sha256 must be null or a lowercase SHA-256")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def audit_recovery(manifest):
    """Check metadata and rollback installer; never scan the multi-TB image."""
    recovery = manifest["recovery"]
    observations = {"image_check_evidence": recovery.get("clonezilla_image_check"),
                    "image_payload_verified_by_planner": False}
    blockers, warnings = [], []

    def block(code, message):
        blockers.append({"code": code, "message": message})

    root = Path(recovery["backup_image"])
    disk = recovery["source_disk"]
    required = ["disk", "parts", "clonezilla-img", "efi-nvram.dat",
                f"{disk}-gpt-1st", f"{disk}-gpt-2nd", f"{disk}-mbr",
                f"{disk}p1.vfat-ptcl-img.zst", f"{disk}p2.ext4-ptcl-img.zst"]
    sizes = {}
    for name in required:
        try:
            path = root / name
            if not path.is_file() or path.stat().st_size == 0:
                raise OSError("missing or empty")
            sizes[name] = path.stat().st_size
        except OSError as exc:
            block("recovery_file_missing", f"Cannot establish {path}: {exc}")
    observations["image_files_bytes"] = sizes
    try:
        if (root / "disk").read_text().strip() != disk:
            block("recovery_source_mismatch", "Image disk metadata does not match the recorded source.")
        if (root / "parts").read_text().split() != [disk + "p1", disk + "p2"]:
            block("recovery_parts_mismatch", "Image partition metadata differs from EFI/root layout.")
        if recovery["source_disk_serial"] not in (root / "clonezilla-img").read_text():
            block("recovery_serial_mismatch", "Recorded system serial is absent from Clonezilla log.")
    except OSError as exc:
        block("recovery_metadata_unreadable", str(exc))
    try:
        result = subprocess.run(["/usr/bin/findmnt", "-no", "UUID", "--target", str(root)],
                                capture_output=True, text=True, timeout=8, env=SAFE_ENV)
        actual_uuid = result.stdout.strip()
        observations["backup_filesystem_uuid"] = actual_uuid
        if result.returncode or actual_uuid != recovery["backup_filesystem_uuid"]:
            block("recovery_mount_mismatch", "Image location is not on the recorded Data2 filesystem.")
    except (OSError, subprocess.TimeoutExpired) as exc:
        block("recovery_mount_unavailable", str(exc))
    if recovery.get("clonezilla_image_check") != "user_reported_passed_2026-09-19":
        block("recovery_check_unrecorded", "The successful Clonezilla image check has not been recorded.")
    if not Path(recovery["session_recovery"]).is_dir():
        block("session_recovery_missing", "The recorded session recovery directory is unavailable.")
    installer = Path(recovery["rollback_installer"])
    expected_digest = recovery.get("rollback_installer_sha256")
    observations["rollback_installer"] = str(installer)
    observations["rollback_installer_verified"] = False
    if not installer.is_file():
        block("rollback_installer_missing", f"Preserve the matching 535 installer at {installer}.")
    elif not expected_digest:
        block("rollback_installer_checksum_missing", "Record an independently verified SHA-256 for the saved installer.")
    else:
        try:
            actual = sha256(installer)
            observations["rollback_installer_sha256"] = actual
            if actual != expected_digest:
                block("rollback_installer_checksum_mismatch", "Saved rollback installer checksum differs from manifest.")
            else:
                observations["rollback_installer_verified"] = True
        except OSError as exc:
            block("rollback_installer_unreadable", str(exc))
    warnings.append({"code": "image_check_attested", "message":
                     "Clonezilla check passed according to the user; this planner checks metadata and file sizes, not image contents or restore success."})
    return {"observations": observations, "blockers": blockers, "warnings": warnings}


def collect(manifest, work_dir, refresh=False):
    collectors = {"host": lambda: audit_host(manifest),
                  "recovery": lambda: audit_recovery(manifest),
                  "apt": lambda: build_apt_plan(manifest, work_dir / "apt", refresh=refresh)}
    sections = {}
    for name, collector in collectors.items():
        try:
            sections[name] = collector()
        except Exception as exc:
            sections[name] = {"observations": {}, "warnings": [], "blockers": [
                {"code": "collector_failed", "message": f"{type(exc).__name__}: {exc}"}]}
    blockers = [{"section": name, **issue} for name, data in sections.items() for issue in data["blockers"]]
    warnings = [{"section": name, **issue} for name, data in sections.items() for issue in data["warnings"]]
    return {"schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "plan_only", "installation_ready": False,
            "status": "blocked" if blockers else "review_required",
            "qualification": "Runtime/GPUDirect/camera qualification and a reviewed maintenance procedure remain required.",
            "sections": sections, "blockers": blockers, "warnings": warnings}


def summary(report):
    changes = report["sections"].get("apt", {}).get("transactions", [])
    lines = [f"NVIDIA migration plan: {report['status']}",
             "Planning only: no driver, package, DKMS, service, or boot changes were performed.",
             f"APT simulation: {len(changes)} proposed package transitions."]
    for issue in report["blockers"]:
        lines.append(f"BLOCK [{issue['section']}/{issue['code']}]: {issue['message']}")
    for issue in report["warnings"]:
        lines.append(f"NOTE [{issue['section']}/{issue['code']}]: {issue['message']}")
    lines.append(report["qualification"])
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, help="new private artifact directory; must not already exist")
    parser.add_argument("--refresh-metadata", action="store_true", help="fetch signed metadata into the private artifact directory")
    args = parser.parse_args(argv)
    if args.refresh_metadata and not args.output_dir:
        parser.error("--refresh-metadata requires --output-dir")
    try:
        raw_manifest = args.manifest.read_bytes()
        manifest = json.loads(raw_manifest)
        validate_manifest(manifest)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    temporary = None
    try:
        if args.output_dir:
            work = args.output_dir.absolute()
            if any(x in str(work) for x in ('"', '\\', '\n', '\r')):
                parser.error("output path must not contain quotes, backslashes, or newlines")
            if any(parent.is_symlink() for parent in (work, *work.parents)):
                parser.error("output path must not contain symlinks")
            work.mkdir(mode=0o700)  # Refuse an existing directory or symlink.
        else:
            temporary = tempfile.TemporaryDirectory(prefix="pancake-driver-plan-")
            work = Path(temporary.name)
        (work / "manifest.json").write_bytes(raw_manifest)
        report = collect(manifest, work, args.refresh_metadata)
        report["manifest_sha256"] = hashlib.sha256(raw_manifest).hexdigest()
        report["artifacts_persisted"] = args.output_dir is not None
        (work / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        changes = report["sections"].get("apt", {}).get("transactions", [])
        columns = ("action", "package", "from_version", "to_version")
        (work / "packages.tsv").write_text("\t".join(columns) + "\n" + "".join(
            "\t".join(str(change.get(key) or "-") for key in columns) + "\n" for change in changes))
        rendered = summary(report)
        (work / "summary.txt").write_text(rendered)
        hashes = {name: sha256(work / name) for name in ("manifest.json", "report.json", "summary.txt", "packages.tsv")}
        (work / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for name, digest in hashes.items()))
        print(rendered, end="")
        if args.output_dir:
            print(f"Artifacts: {work}")
        return 2 if report["blockers"] else 0
    except OSError as exc:
        parser.error(str(exc))
    finally:
        if temporary:
            temporary.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
