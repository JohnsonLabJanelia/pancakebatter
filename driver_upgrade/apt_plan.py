"""Build and validate an isolated, simulation-only NVIDIA APT transaction."""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


NVIDIA_REPOSITORY = (
    "https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2204/x86_64/"
)
NVIDIA_KEY_URL = NVIDIA_REPOSITORY + "3bf863cc.pub"
NVIDIA_KEY_FINGERPRINT = "EB693B3035CD5710E231E123A4B469963BF863CC"
UBUNTU_KEYRING = Path("/usr/share/keyrings/ubuntu-archive-keyring.gpg")
HOST_STATUS = Path("/var/lib/dpkg/status")

# Exact R610 proprietary packages reached from cuda-drivers/nvidia-driver.
R610_COMPONENTS = {
    "cuda-drivers", "libegl-nvidia0", "libglx-nvidia0", "libnvidia-cfg1",
    "libnvidia-compute", "libnvidia-decode", "libnvidia-encode", "libnvidia-extra",
    "libnvidia-fbc1", "libnvidia-gl", "libnvidia-gpucomp", "libgles-nvidia1",
    "libgles-nvidia2", "libxnvctrl0", "nvidia-dkms", "nvidia-driver",
    "nvidia-firmware", "nvidia-kernel-common", "nvidia-kernel-source",
    "nvidia-modprobe", "nvidia-persistenced", "nvidia-settings",
    "xserver-xorg-video-nvidia",
}
R610_FRAMEWORK_VERSIONS = {
    "libnvidia-egl-gbm1": "1.1.4-2ubuntu1",
    "libnvidia-egl-wayland21": "1.0.2-1ubuntu1",
    "libnvidia-egl-xcb1": "1.0.6-1ubuntu1",
    "libnvidia-egl-xlib1": "1.0.6-1ubuntu1",
}

_INST = re.compile(r"^Inst\s+(\S+)(?:\s+\[([^]]+)\])?\s+\((\S+)(?:\s+.*)?\)$")
_REMV = re.compile(r"^Remv\s+(\S+)(?:\s+\[([^]]+)\])?(?:\s+\(.*\))?$")
_CONF = re.compile(r"^Conf\s+(\S+)\s+\((\S+)(?:\s+.*)?\)$")


def _issue(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _result() -> dict[str, Any]:
    return {
        "observations": [],
        "warnings": [],
        "blockers": [],
        "transactions": [],
        "commands": [],
        "artifacts": {},
    }


def _target(manifest: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
    blockers: list[dict[str, str]] = []
    if manifest.get("schema_version") != 1:
        blockers.append(_issue("manifest_schema", "schema_version must be 1"))
    raw = manifest.get("target")
    if not isinstance(raw, Mapping):
        return {}, blockers + [_issue("manifest_target", "target must be an object")]
    target = dict(raw)
    required = (
        "driver_version",
        "package_version",
        "driver_metapackage",
        "pin_package",
        "dkms_package_version",
        "module_flavor",
        "repository_url",
    )
    for key in required:
        if not isinstance(target.get(key), str) or not target[key].strip():
            blockers.append(_issue("manifest_target", f"target.{key} must be a string"))
    allowed = target.get("allowed_dependency_packages", [])
    if not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed):
        blockers.append(
            _issue("manifest_allowlist", "target.allowed_dependency_packages must be a list of package names")
        )
        target["allowed_dependency_packages"] = []
    if blockers:
        return target, blockers
    if target["module_flavor"] != "proprietary":
        blockers.append(_issue("module_flavor", "only the proprietary R610 module flavor is accepted"))
    if target["repository_url"] != NVIDIA_REPOSITORY:
        blockers.append(_issue("repository_url", f"repository_url must equal {NVIDIA_REPOSITORY}"))
    if not target["package_version"].startswith(target["driver_version"] + "-"):
        blockers.append(_issue("target_version", "package_version does not match driver_version"))
    key_url = target.setdefault("repository_key_url", NVIDIA_KEY_URL)
    fingerprint = str(target.setdefault("repository_key_fingerprint", NVIDIA_KEY_FINGERPRINT))
    target["repository_key_fingerprint"] = fingerprint.replace(" ", "").upper()
    if key_url != NVIDIA_KEY_URL or target["repository_key_fingerprint"] != NVIDIA_KEY_FINGERPRINT:
        blockers.append(_issue("repository_key", "repository key URL/fingerprint is not the pinned NVIDIA key"))
    target.setdefault("pin_package_version", target["package_version"])
    framework = target.get("framework_package_versions")
    if not isinstance(framework, Mapping) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in framework.items()
    ):
        blockers.append(_issue("manifest_framework", "framework_package_versions must map package names to versions"))
    elif dict(framework) != R610_FRAMEWORK_VERSIONS:
        blockers.append(_issue("manifest_framework", "framework_package_versions must contain the vetted R610 versions"))
    dependencies = target.get("dependency_package_versions")
    if not isinstance(dependencies, Mapping) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in dependencies.items()
    ):
        blockers.append(_issue("manifest_dependency_versions", "dependency_package_versions must map package names to versions"))
    elif set(dependencies) != set(target["allowed_dependency_packages"]):
        blockers.append(_issue("manifest_dependency_versions", "allowed dependency names and version pins must match exactly"))
    return target, blockers


def _secure_work_dir(work_dir: Path, *, create: bool) -> tuple[Path, list[dict[str, str]]]:
    raw = Path(work_dir)
    if raw.is_symlink():
        return raw, [_issue("work_dir", "APT work directory must not be a symlink")]
    if not raw.exists() and create:
        try:
            raw.mkdir(mode=0o700)
        except OSError as exc:
            return raw, [_issue("work_dir", f"could not create private APT work directory: {exc}")]
    if not raw.is_dir():
        return raw, [_issue("work_dir", "APT work directory is missing or is not a directory")]
    info = raw.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        return raw, [_issue("work_dir", "APT work directory must be owned by this user with mode 0700")]
    return raw.resolve(), []


def _official_ubuntu_lines() -> tuple[list[str], list[str]]:
    """Normalize only official Ubuntu binary sources; ignore every third party."""
    lines: list[str] = []
    hosts: list[str] = []
    files = [Path("/etc/apt/sources.list"), *Path("/etc/apt/sources.list.d").glob("*.list")]
    for path in files:
        try:
            source_lines = path.read_text().splitlines()
        except OSError:
            continue
        for raw in source_lines:
            fields = raw.strip().split()
            if not fields or fields[0] != "deb":
                continue
            # Skip an existing option block without trying to preserve third-party options.
            index = 1
            if index < len(fields) and fields[index].startswith("["):
                while index < len(fields) and not fields[index].endswith("]"):
                    index += 1
                index += 1
            if len(fields) < index + 3:
                continue
            url = fields[index]
            parsed = urlparse(url)
            if not parsed.hostname or not parsed.hostname.endswith(".ubuntu.com"):
                continue
            if parsed.path.rstrip("/") != "/ubuntu":
                continue
            suite, components = fields[index + 1], fields[index + 2 :]
            line = (
                f"deb [signed-by={UBUNTU_KEYRING}] {url} {suite} "
                + " ".join(components)
            )
            if line not in lines:
                lines.append(line)
            hosts.append(parsed.hostname)
    return lines, sorted(set(hosts))


def _copy_host_ubuntu_lists(destination: Path, hosts: list[str]) -> int:
    copied = 0
    source = Path("/var/lib/apt/lists")
    for item in source.iterdir() if source.is_dir() else ():
        if not item.is_file():
            continue
        if not any(item.name.startswith(host + "_") for host in hosts):
            continue
        target = destination / item.name
        if not target.is_file() or target.stat().st_size != item.stat().st_size:
            shutil.copy2(item, target)
        copied += 1
    return copied


def _apt_options(work_dir: Path) -> list[str]:
    state = work_dir / "state"
    cache = work_dir / "cache"
    return [
        "-o", f"Dir::Etc::sourcelist={work_dir / 'sources.list'}",
        "-o", f"Dir::Etc::sourceparts={work_dir / 'sourceparts'}",
        "-o", f"Dir::Etc::preferences={work_dir / 'preferences'}",
        "-o", f"Dir::Etc::preferencesparts={work_dir / 'preferences.d'}",
        "-o", f"Dir::Etc::trusted={work_dir / 'keyrings/nvidia.gpg'}",
        "-o", f"Dir::Etc::trustedparts={work_dir / 'trusted.gpg.d'}",
        "-o", f"Dir::State={state}",
        "-o", f"Dir::State::lists={state / 'lists'}",
        "-o", f"Dir::State::status={HOST_STATUS}",
        "-o", f"Dir::State::extended_states={state / 'extended_states'}",
        "-o", f"Dir::Cache={cache}",
        "-o", f"Dir::Cache::archives={cache / 'archives'}",
        "-o", f"Dir::Cache::pkgcache={cache / 'pkgcache.bin'}",
        "-o", f"Dir::Cache::srcpkgcache={cache / 'srcpkgcache.bin'}",
        "-o", f"Dir::Log={work_dir / 'logs'}",
        "-o", "Debug::NoLocking=1",
        "-o", "APT::Get::List-Cleanup=0",
    ]


def _subprocess_env(work_dir: Path, *, apt: bool = False) -> dict[str, str]:
    env = {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LC_ALL": "C",
        "LANG": "C",
        "HOME": str(work_dir / "home"),
        "GNUPGHOME": str(work_dir / "gnupg"),
    }
    if apt:
        env["APT_CONFIG"] = str(work_dir / "apt-bootstrap.conf")
    return env


def _run(argv: list[str], work_dir: Path, *, apt: bool = False, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        argv, text=True, capture_output=True, check=False, timeout=timeout,
        env=_subprocess_env(work_dir, apt=apt),
    )


def _save_process(work_dir: Path, name: str, completed: subprocess.CompletedProcess[str]) -> None:
    (work_dir / f"{name}.stdout").write_text(completed.stdout)
    (work_dir / f"{name}.stderr").write_text(completed.stderr)


def _fingerprints(key_file: Path, home: Path) -> set[str]:
    try:
        completed = _run(
            ["gpg", "--batch", "--no-options", "--homedir", str(home),
             "--with-colons", "--show-keys", str(key_file)],
            home.parent, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return set()
    if completed.returncode:
        return set()
    return {
        line.split(":")[9].upper()
        for line in completed.stdout.splitlines()
        if line.startswith("fpr:") and len(line.split(":")) > 9
    }


def _install_key(target: Mapping[str, Any], work_dir: Path) -> tuple[bool, str]:
    armored = work_dir / "keyrings" / "nvidia.asc"
    binary = work_dir / "keyrings" / "nvidia.gpg"
    try:
        with urllib.request.urlopen(target["repository_key_url"], timeout=30) as response:
            data = response.read(1_000_001)
            if len(data) > 1_000_000:
                return False, "NVIDIA repository key response was unexpectedly large"
            armored.write_bytes(data)
    except Exception as exc:  # urllib raises several unrelated transport exceptions.
        return False, f"could not fetch NVIDIA repository key: {exc}"
    fingerprints = _fingerprints(armored, work_dir / "gnupg")
    if target["repository_key_fingerprint"] not in fingerprints:
        return False, "downloaded NVIDIA repository key fingerprint did not match the pinned fingerprint"
    try:
        completed = _run(
            ["gpg", "--batch", "--yes", "--dearmor", "--output", str(binary), str(armored)],
            work_dir, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "gpg timed out while dearmoring the NVIDIA repository key"
    if completed.returncode:
        return False, f"could not dearmor NVIDIA repository key: {completed.stderr.strip()}"
    return True, target["repository_key_fingerprint"]


def _write_preferences(target: Mapping[str, Any], path: Path) -> None:
    groups = [
        (sorted(R610_COMPONENTS), target["package_version"]),
        ([target["pin_package"]], target["pin_package_version"]),
        (["dkms"], target["dkms_package_version"]),
    ]
    groups.extend(([package], version) for package, version in target["framework_package_versions"].items())
    groups.extend(([package], version) for package, version in target["dependency_package_versions"].items())
    records = []
    for packages, version in groups:
        names = " ".join(packages)
        records.extend((
            f"Package: {names}\nPin: version {version}\nPin-Priority: 1001",
            f"Package: {names}\nPin: version *\nPin-Priority: -1",
        ))
    path.write_text("\n\n".join(records) + "\n")


def _has_package_lists(directory: Path, token: str) -> bool:
    return any(token in path.name and "Packages" in path.name for path in directory.glob("*"))


def _audit_apt_config(work_dir: Path) -> tuple[bool, str]:
    """Prove the child APT process did not load host hook configuration."""
    try:
        completed = _run(["apt-config", "dump"], work_dir, apt=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, "private apt-config audit timed out"
    _save_process(work_dir, "apt-config", completed)
    if completed.returncode:
        return False, "private apt-config audit failed"
    hooks = ("DPkg::Pre-Invoke", "DPkg::Post-Invoke", "DPkg::Pre-Install-Pkgs", "APT::Update::Post-Invoke")
    if any(line.startswith(hooks) for line in completed.stdout.splitlines()):
        return False, "host APT hooks leaked into the private configuration"
    expected = {
        f'Dir::Etc::main "{work_dir / "apt-main.conf"}";',
        f'Dir::Etc::parts "{work_dir / "apt.conf.d"}";',
    }
    if not expected.issubset(set(completed.stdout.splitlines())):
        return False, "private APT main/parts configuration was not active"
    return True, "private APT configuration contains no package/update hooks"


def prepare_apt(
    manifest: Mapping[str, Any], work_dir: Path, *, refresh: bool = False
) -> dict[str, Any]:
    result = _result()
    target, blockers = _target(manifest)
    result["blockers"].extend(blockers)
    if blockers:
        return result
    work_dir, directory_blockers = _secure_work_dir(work_dir, create=True)
    result["blockers"].extend(directory_blockers)
    if directory_blockers:
        return result
    if not HOST_STATUS.is_file() or HOST_STATUS.stat().st_size == 0:
        result["blockers"].append(_issue("host_status", "host dpkg status is missing or empty"))
        return result
    for path in (
        work_dir / "state/lists/partial", work_dir / "cache/archives/partial",
        work_dir / "keyrings", work_dir / "gnupg", work_dir / "home",
        work_dir / "apt.conf.d", work_dir / "sourceparts",
        work_dir / "preferences.d", work_dir / "trusted.gpg.d", work_dir / "logs",
    ):
        path.mkdir(parents=True, exist_ok=True)
    (work_dir / "gnupg").chmod(0o700)
    (work_dir / "apt-main.conf").touch(exist_ok=True)
    (work_dir / "apt-bootstrap.conf").write_text(
        f'Dir::Etc::main "{work_dir / "apt-main.conf"}";\n'
        f'Dir::Etc::parts "{work_dir / "apt.conf.d"}";\n'
    )
    config_ok, config_detail = _audit_apt_config(work_dir)
    if not config_ok:
        result["blockers"].append(_issue("apt_config", config_detail))
        return result
    result["observations"].append(_issue("apt_config", config_detail))
    (work_dir / "state/extended_states").touch(exist_ok=True)
    ubuntu_lines, hosts = _official_ubuntu_lines()
    if not ubuntu_lines or not UBUNTU_KEYRING.is_file():
        result["blockers"].append(_issue("ubuntu_sources", "official Ubuntu sources/keyring are unavailable"))
        return result
    sources = ubuntu_lines + [
        f"deb [arch=amd64 signed-by={work_dir / 'keyrings/nvidia.gpg'}] "
        f"{target['repository_url']} /"
    ]
    (work_dir / "sources.list").write_text("\n".join(sources) + "\n")
    (work_dir / "nvidia.list").write_text(sources[-1] + "\n")
    _write_preferences(target, work_dir / "preferences")
    copied = _copy_host_ubuntu_lists(work_dir / "state/lists", hosts)
    result["observations"].append(
        _issue("ubuntu_metadata_seed", f"copied {copied} cached official Ubuntu metadata files")
    )
    result["warnings"].append(_issue(
        "ubuntu_metadata_cached",
        "Ubuntu dependency metadata is copied from the host cache, not refreshed; "
        "this simulation does not prove that every .deb can still be downloaded."
    ))
    binary_key = work_dir / "keyrings/nvidia.gpg"
    if binary_key.is_file():
        if target["repository_key_fingerprint"] not in _fingerprints(binary_key, work_dir / "gnupg"):
            result["blockers"].append(_issue("repository_key", "isolated NVIDIA key fingerprint is invalid"))
        else:
            result["observations"].append(
                _issue("repository_key", f"verified {target['repository_key_fingerprint']}")
            )
    elif refresh:
        ok, detail = _install_key(target, work_dir)
        if not ok:
            result["blockers"].append(_issue("repository_key", detail))
        else:
            result["observations"].append(_issue("repository_key", f"verified {detail}"))
    else:
        result["blockers"].append(_issue("repository_key_missing", "isolated NVIDIA key is not cached"))
    if refresh and not result["blockers"]:
        command = [
            "apt-get", *_apt_options(work_dir),
            "-o", f"Dir::Etc::sourcelist={work_dir / 'nvidia.list'}", "update",
        ]
        result["commands"].append(command)
        try:
            completed = _run(command, work_dir, apt=True, timeout=180)
        except subprocess.TimeoutExpired:
            result["blockers"].append(_issue("metadata_refresh_timeout", "isolated apt-get update timed out"))
            completed = None
        if completed is None:
            return result
        _save_process(work_dir, "update", completed)
        if completed.returncode:
            tail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
            result["blockers"].append(
                _issue("metadata_refresh_failed", tail[0] if tail else "isolated apt-get update failed")
            )
    lists = work_dir / "state/lists"
    if not _has_package_lists(lists, "developer.download.nvidia.com"):
        result["blockers"].append(
            _issue("metadata_missing", "signed NVIDIA package metadata is absent; use refresh=True")
        )
    if not any(_has_package_lists(lists, host + "_") for host in hosts):
        result["blockers"].append(_issue("metadata_missing", "cached official Ubuntu package metadata is absent"))
    result["artifacts"] = {
        "work_dir": str(work_dir),
        "sources": str(work_dir / "sources.list"),
        "preferences": str(work_dir / "preferences"),
        "nvidia_keyring": str(binary_key),
        "update_stdout": str(work_dir / "update.stdout"),
        "update_stderr": str(work_dir / "update.stderr"),
        "apt_config_dump": str(work_dir / "apt-config.stdout"),
    }
    return result


def _parse_simulation(output: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    transactions: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    configured: set[str] = set()
    for line in output.splitlines():
        if line.startswith("Inst "):
            match = _INST.match(line)
            if not match:
                blockers.append(_issue("simulation_malformed", f"could not parse: {line}"))
                continue
            package, old, new = match.groups()
            transactions.append({
                "action": "upgraded" if old else "installed",
                "package": package, "from_version": old, "to_version": new,
            })
        elif line.startswith("Remv "):
            match = _REMV.match(line)
            if not match:
                blockers.append(_issue("simulation_malformed", f"could not parse: {line}"))
                continue
            transactions.append({
                "action": "removed", "package": match.group(1),
                "from_version": match.group(2), "to_version": None,
            })
        elif line.startswith("Conf "):
            match = _CONF.match(line)
            if not match:
                blockers.append(_issue("simulation_malformed", f"could not parse: {line}"))
            else:
                configured.add(match.group(1))
    pending = {x["package"] for x in transactions if x["action"] != "removed"} - configured
    if pending:
        blockers.append(
            _issue("simulation_truncated", "missing Conf records for: " + ", ".join(sorted(pending)))
        )
    if "NOTE: This is only a simulation!" not in output:
        blockers.append(_issue("simulation_marker", "apt simulation marker is missing"))
    return transactions, blockers


def _base_package(package: str) -> str:
    return package.split(":", 1)[0]


def _validate_transaction(
    transactions: list[dict[str, Any]], target: Mapping[str, Any]
) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    framework = target.get("framework_package_versions", R610_FRAMEWORK_VERSIONS)
    dependency_versions = target.get("dependency_package_versions", {})
    required = R610_COMPONENTS | {target["driver_metapackage"], target["pin_package"], "dkms"} | set(framework)
    allowed = required | set(target.get("allowed_dependency_packages", []))
    for change in transactions:
        package = _base_package(change["package"])
        version = change["to_version"]
        if change["action"] == "removed":
            blockers.append(_issue("package_removal", f"simulation removes {package}"))
            continue
        if package not in allowed:
            blockers.append(_issue("unallowlisted_change", f"simulation changes unallowlisted package {package}"))
        if package.startswith(("linux-image", "linux-headers", "linux-modules", "linux-generic", "linux-hwe")):
            blockers.append(_issue("kernel_change", f"simulation changes kernel package {package}"))
        if any(token in package for token in ("mlnx", "ofed", "nvidia-peermem", "nvidia-peer-memory")) or package.startswith(("rdma-", "ibverbs-")):
            blockers.append(_issue("ofed_change", f"simulation changes OFED/peer-memory package {package}"))
        if package.startswith("cuda-") and package != target["driver_metapackage"]:
            blockers.append(_issue("cuda_toolkit_change", f"simulation changes CUDA package {package}"))
        if package.startswith(("libcudart", "libnvinfer", "tensorrt", "nsight-")):
            blockers.append(_issue("cuda_toolkit_change", f"simulation changes CUDA/TensorRT package {package}"))
        if package.startswith(("nvidia-", "libnvidia-")) and "open" in package:
            blockers.append(_issue("open_module", f"simulation selects open module package {package}"))
        looks_nvidia = package.startswith((
            "nvidia-", "libnvidia-", "libegl-nvidia", "libglx-nvidia",
            "libgles-nvidia", "xserver-xorg-video-nvidia",
        ))
        if looks_nvidia and package not in required:
            blockers.append(_issue("unvetted_nvidia_component", f"simulation selects unvetted NVIDIA package {package}"))
        if package == "dkms" and version != target["dkms_package_version"]:
            blockers.append(_issue("dkms_version", f"dkms target {version} is not {target['dkms_package_version']}"))
        if package in R610_COMPONENTS and version != target["package_version"]:
            blockers.append(_issue("nvidia_version", f"{package} target {version} is not {target['package_version']}"))
        if package == target["pin_package"] and version != target["pin_package_version"]:
            blockers.append(_issue("nvidia_version", f"{package} target {version} is not {target['pin_package_version']}"))
        if package in framework and version != framework[package]:
            blockers.append(_issue("framework_version", f"{package} target {version} is not {framework[package]}"))
        if package in dependency_versions and version != dependency_versions[package]:
            blockers.append(_issue("dependency_version", f"{package} target {version} is not {dependency_versions[package]}"))
    return blockers


def _installed_versions(status_file: Path = HOST_STATUS) -> dict[str, str]:
    installed: dict[str, str] = {}
    for stanza in status_file.read_text(errors="replace").split("\n\n"):
        fields = {}
        for line in stanza.splitlines():
            if ": " in line:
                key, value = line.split(": ", 1)
                fields[key] = value
        if fields.get("Status") == "install ok installed" and fields.get("Package") and fields.get("Version"):
            installed[fields["Package"]] = fields["Version"]
    return installed


def _validate_required_roots(
    transactions: list[dict[str, Any]], target: Mapping[str, Any], installed: Mapping[str, str]
) -> list[dict[str, str]]:
    final = dict(installed)
    for change in transactions:
        package = _base_package(change["package"])
        if change["action"] == "removed":
            final.pop(package, None)
        else:
            final[package] = change["to_version"]
    desired = {
        target["driver_metapackage"]: target["package_version"],
        target["pin_package"]: target["pin_package_version"],
        "dkms": target["dkms_package_version"],
    }
    return [
        _issue("required_root", f"final {package} version {final.get(package)!r} is not {version}")
        for package, version in desired.items() if final.get(package) != version
    ]


def simulate_apt(manifest: Mapping[str, Any], work_dir: Path) -> dict[str, Any]:
    result = _result()
    target, blockers = _target(manifest)
    result["blockers"].extend(blockers)
    work_dir, directory_blockers = _secure_work_dir(work_dir, create=False)
    result["blockers"].extend(directory_blockers)
    blockers = blockers + directory_blockers
    lists = work_dir / "state/lists"
    if blockers or not _has_package_lists(lists, "developer.download.nvidia.com"):
        if not blockers:
            result["blockers"].append(_issue("metadata_missing", "NVIDIA package metadata is absent"))
        return result
    packages = [
        f"{target['pin_package']}={target['pin_package_version']}",
        f"{target['driver_metapackage']}={target['package_version']}",
        f"dkms={target['dkms_package_version']}",
    ]
    # --simulate is the no-payload boundary.  --no-download paradoxically makes
    # apt require every .deb to exist in the cache before it will print a plan.
    command = ["apt-get", *_apt_options(work_dir), "--simulate"]
    if target.get("install_recommends") is False:
        command.append("--no-install-recommends")
    command.extend(["install", *packages])
    result["commands"].append(command)
    if not (work_dir / "apt-bootstrap.conf").is_file():
        result["blockers"].append(_issue("apt_config_missing", "private APT bootstrap config is absent"))
        return result
    try:
        completed = _run(command, work_dir, apt=True, timeout=90)
    except subprocess.TimeoutExpired:
        result["blockers"].append(_issue("simulation_timeout", "apt-get simulation timed out"))
        return result
    _save_process(work_dir, "simulate", completed)
    result["artifacts"] = {
        "simulate_stdout": str(work_dir / "simulate.stdout"),
        "simulate_stderr": str(work_dir / "simulate.stderr"),
    }
    transactions, parse_blockers = _parse_simulation(completed.stdout)
    result["transactions"] = transactions
    result["blockers"].extend(parse_blockers)
    if completed.returncode:
        tail = (completed.stderr or completed.stdout).strip().splitlines()[-1:]
        result["blockers"].append(
            _issue("simulation_failed", tail[0] if tail else "apt-get simulation failed")
        )
    result["blockers"].extend(_validate_transaction(transactions, target))
    result["blockers"].extend(_validate_required_roots(transactions, target, _installed_versions()))
    if not result["blockers"]:
        result["observations"].append(
            _issue("simulation_valid", f"validated {len(transactions)} package transitions")
        )
    return result


def build_apt_plan(
    manifest: Mapping[str, Any], work_dir: Path, refresh: bool = False
) -> dict[str, Any]:
    prepared = prepare_apt(manifest, work_dir, refresh=refresh)
    if prepared["blockers"]:
        return prepared
    simulated = simulate_apt(manifest, work_dir)
    simulated["observations"] = prepared["observations"] + simulated["observations"]
    simulated["warnings"] = prepared["warnings"] + simulated["warnings"]
    simulated["commands"] = prepared["commands"] + simulated["commands"]
    simulated["artifacts"] = prepared["artifacts"] | simulated["artifacts"]
    return simulated
