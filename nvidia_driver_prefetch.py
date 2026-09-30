#!/usr/bin/env python3
"""Freeze the reviewed NVIDIA driver transaction without installing anything."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urljoin, urlparse

try:
    from driver_upgrade import apt_plan
except ModuleNotFoundError:
    sys.path.insert(0, str(Path.home() / "pancakebatter"))
    from driver_upgrade import apt_plan


TARGET_COUNT = 22
ROLLBACK_DKMS = ("dkms", "2.8.7-2ubuntu2")
UBUNTU_KEYRING = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _parse_packages(text: str) -> list[dict[str, str]]:
    records = []
    for paragraph in text.split("\n\n"):
        fields: dict[str, str] = {}
        key = None
        for line in paragraph.splitlines():
            if line.startswith((" ", "\t")) and key:
                fields[key] += "\n" + line[1:]
            elif ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if fields.get("Package"):
            records.append(fields)
    return records


def _select_record(
    records: list[dict[str, str]], package: str, version: str
) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    matches = [
        row for row in records
        if row.get("Package") == package
        and row.get("Version") == version
        and row.get("Architecture") in {"amd64", "all"}
    ]
    if len(matches) != 1:
        return None, [_issue(
            "metadata_ambiguous",
            f"expected one amd64/all metadata record for {package}={version}, found {len(matches)}",
        )]
    required = ("Filename", "Size", "SHA256")
    missing = [field for field in required if not matches[0].get(field)]
    if missing:
        return None, [_issue("metadata_incomplete", f"{package} metadata lacks {', '.join(missing)}")]
    return matches[0], []


def _signed_index_digest(inrelease: str, relative_name: str) -> tuple[str, int] | None:
    in_sha256 = False
    for line in inrelease.splitlines():
        if line == "SHA256:":
            in_sha256 = True
            continue
        if in_sha256 and line and not line.startswith(" "):
            break
        if in_sha256:
            fields = line.split()
            if len(fields) == 3 and fields[2] == relative_name:
                try:
                    return fields[0], int(fields[1])
                except ValueError:
                    return None
    return None


def _verify_signed_index(inrelease: Path, packages: Path, relative_name: str) -> list[dict[str, str]]:
    expected = _signed_index_digest(inrelease.read_text(), relative_name)
    if expected is None:
        return [_issue("signed_index", f"signed metadata has no SHA256 entry for {relative_name}")]
    digest, size = expected
    if packages.stat().st_size != size or _sha256(packages) != digest:
        return [_issue("signed_index", f"{packages.name} does not match signed SHA256/size")]
    return []


def _load_catalog(plan_dir: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    try:
        catalog = _read_json(plan_dir / "artifact_checksums.json")
    except (OSError, ValueError) as exc:
        return {}, [_issue("checksum_catalog", f"cannot read artifact_checksums.json: {exc}")]
    if not isinstance(catalog, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in catalog.items()):
        return {}, [_issue("checksum_catalog", "artifact checksum catalog is malformed")]
    return catalog, []


def _verify_catalog_file(plan_dir: Path, catalog: Mapping[str, str], relative: str) -> list[dict[str, str]]:
    path = plan_dir / relative
    if relative not in catalog or not path.is_file():
        return [_issue("artifact_checksum", f"missing catalog entry or artifact: {relative}")]
    if _sha256(path) != catalog[relative]:
        return [_issue("artifact_checksum", f"artifact checksum mismatch: {relative}")]
    return []


def _parse_top_sums(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        fields = line.split(None, 1)
        if len(fields) == 2:
            result[fields[1].lstrip("* ")] = fields[0]
    return result


def _all_report_blockers(value: Any) -> list[Any]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "blockers" and isinstance(child, list):
                found.extend(child)
            else:
                found.extend(_all_report_blockers(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(_all_report_blockers(child))
    return found


def _parse_packages_tsv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as stream:
        rows = list(csv.DictReader(stream, delimiter="\t"))
    return [{
        "action": row["action"], "package": row["package"],
        "from_version": None if row["from_version"] == "-" else row["from_version"],
        "to_version": row["to_version"],
    } for row in rows]


def _validate_plan(plan_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[dict[str, Any]], dict[str, str], list[dict[str, str]]]:
    blockers: list[dict[str, str]] = []
    catalog, issues = _load_catalog(plan_dir)
    blockers.extend(issues)
    for relative in ("manifest.json", "report.json", "packages.tsv", "SHA256SUMS"):
        blockers.extend(_verify_catalog_file(plan_dir, catalog, relative))
    if blockers:
        return None, None, [], catalog, blockers
    top_sums = _parse_top_sums(plan_dir / "SHA256SUMS")
    for relative in ("manifest.json", "report.json", "packages.tsv"):
        if top_sums.get(relative) != _sha256(plan_dir / relative):
            blockers.append(_issue("plan_checksum", f"SHA256SUMS mismatch for {relative}"))
    try:
        manifest = _read_json(plan_dir / "manifest.json")
        report = _read_json(plan_dir / "report.json")
        tsv_transactions = _parse_packages_tsv(plan_dir / "packages.tsv")
    except (OSError, ValueError, KeyError) as exc:
        return None, None, [], catalog, blockers + [_issue("plan_parse", str(exc))]
    if report.get("status") != "review_required":
        blockers.append(_issue("report_status", "report status must be review_required"))
    if _all_report_blockers(report):
        blockers.append(_issue("report_blockers", "reviewed report contains blockers"))
    apt_section = report.get("sections", {}).get("apt", {})
    transactions = apt_section.get("transactions")
    if not isinstance(transactions, list) or transactions != tsv_transactions:
        blockers.append(_issue("transaction_mismatch", "report and packages.tsv transactions differ"))
        transactions = []
    if len(transactions) != TARGET_COUNT:
        blockers.append(_issue("target_count", f"expected {TARGET_COUNT} target transitions, found {len(transactions)}"))
    names = [row.get("package") for row in transactions]
    if len(set(names)) != len(names) or any(row.get("action") == "removed" for row in transactions):
        blockers.append(_issue("target_set", "target transaction has duplicates or removals"))
    target, target_issues = apt_plan._target(manifest)
    blockers.extend(target_issues)
    if not target_issues:
        blockers.extend(apt_plan._validate_transaction(transactions, target))
    sim_rel = "apt/simulate.stdout"
    blockers.extend(_verify_catalog_file(plan_dir, catalog, sim_rel))
    if not blockers:
        parsed, parse_issues = apt_plan._parse_simulation((plan_dir / sim_rel).read_text())
        blockers.extend(parse_issues)
        if parsed != transactions:
            blockers.append(_issue("transaction_mismatch", "saved APT simulation differs from report"))
    return manifest, report, transactions, catalog, blockers


def _env(apt_root: Path) -> dict[str, str]:
    return {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C",
        "HOME": str(apt_root / "home"), "GNUPGHOME": str(apt_root / "gnupg"),
        "APT_CONFIG": str(apt_root / "apt-bootstrap.conf"),
    }


def _run(argv: list[str], apt_root: Path, cwd: Path | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, cwd=cwd, env=_env(apt_root), text=True, capture_output=True,
        check=False, timeout=timeout,
    )


def _apt_options(plan_apt: Path, apt_root: Path, sources: Path, preferences: Path) -> list[str]:
    return [
        "-o", f"Dir::Etc::sourcelist={sources}",
        "-o", f"Dir::Etc::sourceparts={apt_root / 'sourceparts'}",
        "-o", f"Dir::Etc::preferences={preferences}",
        "-o", f"Dir::Etc::preferencesparts={apt_root / 'preferences.d'}",
        "-o", f"Dir::Etc::trusted={plan_apt / 'keyrings/nvidia.gpg'}",
        "-o", f"Dir::Etc::trustedparts={apt_root / 'trusted.gpg.d'}",
        "-o", f"Dir::State={apt_root / 'state'}",
        "-o", f"Dir::State::lists={plan_apt / 'state/lists'}",
        "-o", "Dir::State::status=/var/lib/dpkg/status",
        "-o", f"Dir::State::extended_states={apt_root / 'state/extended_states'}",
        "-o", f"Dir::Cache={apt_root / 'cache'}",
        "-o", f"Dir::Cache::archives={apt_root / 'cache/archives'}",
        "-o", f"Dir::Log={apt_root / 'logs'}",
        "-o", "Debug::NoLocking=1", "-o", "APT::Get::List-Cleanup=0",
    ]


def _prepare_output(output_dir: Path) -> tuple[Path | None, list[dict[str, str]]]:
    output_dir = output_dir.absolute()
    if any(x in str(output_dir) for x in ('"', '\\', '\n', '\r')):
        return None, [_issue("output_path", "output path cannot contain APT configuration delimiters")]
    if any(p.is_symlink() for p in (output_dir, *output_dir.parents)):
        return None, [_issue("output_path", "output path cannot contain symlinks")]
    if output_dir.exists() or output_dir.is_symlink():
        return None, [_issue("output_exists", "output directory must be new")]
    try:
        output_dir.mkdir(mode=0o700)
    except OSError as exc:
        return None, [_issue("output_create", str(exc))]
    info = output_dir.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        return None, [_issue("output_permissions", "output directory must be owned by this user with mode 0700")]
    return output_dir.resolve(), []


def _prepare_apt_sandbox(plan_apt: Path, output: Path) -> tuple[Path, list[dict[str, str]]]:
    root = output / ".apt"
    for path in (
        root / "apt.conf.d", root / "sourceparts", root / "preferences.d",
        root / "trusted.gpg.d", root / "state", root / "cache/archives",
        root / "logs", root / "home", root / "gnupg",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (root / "gnupg").chmod(0o700)
    (root / "state/extended_states").touch()
    (root / "apt-main.conf").touch()
    (root / "empty-preferences").touch()
    (root / "apt-bootstrap.conf").write_text(
        f'Dir::Etc::main "{root / "apt-main.conf"}";\n'
        f'Dir::Etc::parts "{root / "apt.conf.d"}";\n'
    )
    ubuntu_lines = [
        line for line in (plan_apt / "sources.list").read_text().splitlines()
        if line.startswith("deb ") and "developer.download.nvidia.com" not in line
    ]
    (root / "ubuntu.list").write_text("\n".join(ubuntu_lines) + "\n")
    audit = _run(["apt-config", "dump"], root, timeout=30)
    (root / "apt-config.stdout").write_text(audit.stdout)
    (root / "apt-config.stderr").write_text(audit.stderr)
    hooks = ("DPkg::Pre-Invoke", "DPkg::Post-Invoke", "DPkg::Pre-Install-Pkgs", "APT::Update::Post-Invoke")
    expected = {
        f'Dir::Etc::main "{root / "apt-main.conf"}";',
        f'Dir::Etc::parts "{root / "apt.conf.d"}";',
    }
    if audit.returncode or any(line.startswith(hooks) for line in audit.stdout.splitlines()):
        return root, [_issue("apt_config", "private APT configuration audit failed or inherited hooks")]
    if not expected.issubset(set(audit.stdout.splitlines())):
        return root, [_issue("apt_config", "private APT main/parts configuration is inactive")]
    return root, []


def _gpg_verify(inrelease: Path, keyring: Path, apt_root: Path, fingerprint: str | None) -> list[dict[str, str]]:
    result = _run(
        ["gpgv", "--status-fd", "1", "--keyring", str(keyring), str(inrelease)],
        apt_root, timeout=30,
    )
    if result.returncode:
        return [_issue("metadata_signature", f"signature verification failed for {inrelease.name}")]
    valid = [line.split()[2] for line in result.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
    if not valid or (fingerprint and fingerprint not in valid):
        return [_issue("metadata_signature", f"expected signing fingerprint absent for {inrelease.name}")]
    return []


def _find_one(directory: Path, token: str, suffix: str) -> tuple[Path | None, list[dict[str, str]]]:
    matches = [path for path in directory.iterdir() if token in path.name and path.name.endswith(suffix)]
    if len(matches) != 1:
        return None, [_issue("metadata_file", f"expected one {token}*{suffix}, found {len(matches)}")]
    return matches[0], []


def _record_url(record: Mapping[str, str], base: str) -> str:
    return urljoin(base.rstrip("/") + "/", record["Filename"].removeprefix("./"))


def _apt_cache_check(
    expected: list[dict[str, str]], plan_apt: Path, apt_root: Path,
    sources: Path, preferences: Path, log_name: str,
) -> list[dict[str, str]]:
    specs = [
        f"{row['Package']}:amd64={row['Version']}" if row["Architecture"] == "amd64"
        else f"{row['Package']}={row['Version']}"
        for row in expected
    ]
    command = ["apt-cache", *_apt_options(plan_apt, apt_root, sources, preferences), "show", *specs]
    completed = _run(command, apt_root, timeout=90)
    (apt_root / f"{log_name}.stdout").write_text(completed.stdout)
    (apt_root / f"{log_name}.stderr").write_text(completed.stderr)
    if completed.returncode:
        return [_issue("apt_cache", f"apt-cache failed for {log_name}")]
    actual = _parse_packages(completed.stdout)
    blockers = []
    for row in expected:
        selected, issues = _select_record(actual, row["Package"], row["Version"])
        blockers.extend(issues)
        if selected and any(selected.get(field) != row.get(field) for field in ("Architecture", "Filename", "Size", "SHA256")):
            blockers.append(_issue("apt_cache", f"apt-cache metadata differs for {row['Package']}"))
    return blockers


def _verify_deb(path: Path, record: Mapping[str, str], apt_root: Path) -> list[dict[str, str]]:
    if path.stat().st_size != int(record["Size"]) or _sha256(path) != record["SHA256"]:
        return [_issue("deb_checksum", f"downloaded bytes do not match signed metadata: {path.name}")]
    completed = _run(
        ["dpkg-deb", "-f", str(path), "Package", "Version", "Architecture"],
        apt_root, timeout=30,
    )
    identity = {}
    for line in completed.stdout.splitlines():
        if ": " in line:
            key, value = line.split(": ", 1)
            identity[key] = value
    expected = {
        "Package": record["Package"], "Version": record["Version"],
        "Architecture": record["Architecture"],
    }
    if completed.returncode or identity != expected:
        return [_issue("deb_identity", f"dpkg-deb identity mismatch: {path.name}")]
    return []


def _download_one(
    record: dict[str, str], destination: Path, plan_apt: Path, apt_root: Path,
    sources: Path, preferences: Path,
) -> tuple[dict[str, Any] | None, list[dict[str, str]], list[str]]:
    destination.mkdir(parents=True, exist_ok=True)
    spec = (
        f"{record['Package']}:amd64={record['Version']}"
        if record["Architecture"] == "amd64" else f"{record['Package']}={record['Version']}"
    )
    command = ["apt-get", *_apt_options(plan_apt, apt_root, sources, preferences), "download", spec]
    before = set(destination.glob("*.deb"))
    completed = _run(command, apt_root, cwd=destination, timeout=600)
    with (apt_root / "download.log").open("a") as log:
        log.write(json.dumps({
            "command": command, "returncode": completed.returncode,
            "stdout": completed.stdout, "stderr": completed.stderr,
        }) + "\n")
    after = set(destination.glob("*.deb"))
    created = after - before
    if completed.returncode or len(created) != 1:
        return None, [_issue("download_failed", f"apt-get download failed or produced unexpected files for {spec}")], command
    path = created.pop()
    issues = _verify_deb(path, record, apt_root)
    if issues:
        return None, issues, command
    locked = {
        "package": record["Package"], "version": record["Version"],
        "architecture": record["Architecture"], "filename": str(path.relative_to(destination.parent.parent)),
        "size": int(record["Size"]), "sha256": record["SHA256"], "url": record["URL"],
    }
    return locked, [], command


def _write_locks(output: Path, plan_dir: Path, manifest_sha: str, target: list[dict[str, Any]], rollback: list[dict[str, Any]]) -> None:
    lock = {
        "schema_version": 1, "source_plan": str(plan_dir),
        "source_manifest_sha256": manifest_sha,
        "target_packages": target, "rollback_packages": rollback,
    }
    (output / "package-lock.json").write_text(json.dumps(lock, indent=2) + "\n")
    with (output / "package-lock.tsv").open("w", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t", lineterminator="\n")
        writer.writerow(("set", "package", "version", "architecture", "filename", "size", "sha256", "url"))
        for set_name, rows in (("target", target), ("rollback", rollback)):
            for row in rows:
                writer.writerow((set_name, *(row[key] for key in ("package", "version", "architecture", "filename", "size", "sha256", "url"))))
    sums = [f"{row['sha256']}  {row['filename']}\n" for row in target + rollback]
    sums.extend(f"{_sha256(output / name)}  {name}\n" for name in ("package-lock.json", "package-lock.tsv"))
    (output / "SHA256SUMS").write_text("".join(sums))


def prefetch_packages(plan_dir: Path, output_dir: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"observations": [], "warnings": [], "blockers": [], "commands": [], "target_packages": [], "rollback_packages": []}
    plan_dir = Path(plan_dir).resolve()
    if not plan_dir.is_dir():
        result["blockers"].append(_issue("plan_dir", "plan directory is missing"))
        return result
    manifest, _, transactions, catalog, blockers = _validate_plan(plan_dir)
    result["blockers"].extend(blockers)
    if blockers or manifest is None:
        return result
    output, blockers = _prepare_output(Path(output_dir))
    result["blockers"].extend(blockers)
    if blockers or output is None:
        return result
    plan_apt = plan_dir / "apt"
    apt_root, blockers = _prepare_apt_sandbox(plan_apt, output)
    result["blockers"].extend(blockers)
    if blockers:
        (output / "prefetch-report.json").write_text(json.dumps(result, indent=2) + "\n")
        return result
    try:
        lists = plan_apt / "state/lists"
        nvidia_release, issues = _find_one(lists, "developer.download.nvidia.com", "_InRelease")
        result["blockers"].extend(issues)
        nvidia_packages, issues = _find_one(lists, "developer.download.nvidia.com", "_Packages")
        result["blockers"].extend(issues)
        ubuntu_release, issues = _find_one(lists, "us.archive.ubuntu.com_ubuntu_dists_jammy", "_InRelease")
        result["blockers"].extend(issues)
        ubuntu_packages, issues = _find_one(lists, "us.archive.ubuntu.com_ubuntu_dists_jammy_main_binary-amd64", "_Packages")
        result["blockers"].extend(issues)
        keyring = plan_apt / "keyrings/nvidia.gpg"
        for path in (nvidia_release, nvidia_packages, ubuntu_release, ubuntu_packages, keyring):
            if path:
                result["blockers"].extend(_verify_catalog_file(plan_dir, catalog, str(path.relative_to(plan_dir))))
        if result["blockers"]:
            return result
        result["blockers"].extend(_gpg_verify(
            nvidia_release, keyring, apt_root, manifest["target"]["repository_key_fingerprint"]
        ))
        result["blockers"].extend(_verify_signed_index(nvidia_release, nvidia_packages, "Packages"))
        result["blockers"].extend(_gpg_verify(ubuntu_release, UBUNTU_KEYRING, apt_root, None))
        result["blockers"].extend(_verify_signed_index(
            ubuntu_release, ubuntu_packages, "main/binary-amd64/Packages"
        ))
        if result["blockers"]:
            return result
        nvidia_records = _parse_packages(nvidia_packages.read_text())
        ubuntu_records = _parse_packages(ubuntu_packages.read_text())
        target_records = []
        for transition in transactions:
            record, issues = _select_record(nvidia_records, transition["package"], transition["to_version"])
            result["blockers"].extend(issues)
            if record:
                record = dict(record)
                record["URL"] = _record_url(record, manifest["target"]["repository_url"])
                target_records.append(record)
        rollback_record, issues = _select_record(ubuntu_records, *ROLLBACK_DKMS)
        result["blockers"].extend(issues)
        if rollback_record:
            rollback_record = dict(rollback_record)
            rollback_record["URL"] = _record_url(rollback_record, "http://us.archive.ubuntu.com/ubuntu/")
        if result["blockers"] or len(target_records) != TARGET_COUNT or rollback_record is None:
            if len(target_records) != TARGET_COUNT:
                result["blockers"].append(_issue("target_count", "authenticated metadata did not yield 22 packages"))
            return result
        result["blockers"].extend(_apt_cache_check(
            target_records, plan_apt, apt_root, plan_apt / "sources.list", plan_apt / "preferences", "apt-cache-target"
        ))
        result["blockers"].extend(_apt_cache_check(
            [rollback_record], plan_apt, apt_root, apt_root / "ubuntu.list", apt_root / "empty-preferences", "apt-cache-rollback"
        ))
        if result["blockers"]:
            return result
        for record in target_records:
            locked, issues, command = _download_one(
                record, output / "packages/target", plan_apt, apt_root,
                plan_apt / "sources.list", plan_apt / "preferences",
            )
            result["commands"].append(command)
            result["blockers"].extend(issues)
            if locked:
                result["target_packages"].append(locked)
            if issues:
                return result
        locked, issues, command = _download_one(
            rollback_record, output / "packages/rollback", plan_apt, apt_root,
            apt_root / "ubuntu.list", apt_root / "empty-preferences",
        )
        result["commands"].append(command)
        result["blockers"].extend(issues)
        if locked:
            result["rollback_packages"].append(locked)
        if not issues:
            _write_locks(
                output, plan_dir, _sha256(plan_dir / "manifest.json"),
                result["target_packages"], result["rollback_packages"],
            )
            result["observations"].append(_issue("prefetch_complete", "verified 22 target packages and one rollback DKMS package"))
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result["blockers"].append(_issue("prefetch_exception", f"prefetch stopped safely: {exc}"))
        return result
    finally:
        (output / "prefetch-report.json").write_text(json.dumps(result, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    result = prefetch_packages(args.plan_dir, args.output_dir)
    print(f"Verified {len(result['target_packages'])} target packages and {len(result['rollback_packages'])} rollback packages.")
    for issue in result["blockers"]:
        print(f"BLOCK [{issue['code']}]: {issue['message']}")
    print(f"Evidence: {args.output_dir}")
    return 0 if not result["blockers"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
