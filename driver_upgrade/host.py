"""Read-only host inspection for an NVIDIA driver migration plan.

The audit deliberately uses small, argv-only subprocesses.  It never invokes
sudo, package managers in mutation modes, DKMS build/install commands, or
project provisioning scripts.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence


Runner = Callable[..., Any]
_TIMEOUT_SECONDS = 8
_OUTPUT_LIMIT = 16_384
_SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
_REQUIRED_EXPECTED = (
    "os_id",
    "os_version_id",
    "architecture",
    "kernel",
    "driver_version",
    "driver_owner",
    "driver_flavor",
    "dkms_version",
    "ofed_package_version",
    "ofed_dkms_version",
    "cuda_symlink_target",
    "tensorrt_root",
    "gpu_uuids",
)


def _default_runner(argv: Sequence[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
    env = {"PATH": _SAFE_PATH, "LC_ALL": "C", "LANG": "C"}
    return subprocess.run(
        list(argv),
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
        env=env,
    )


def _result(value: Any, argv: Sequence[str]) -> dict[str, Any]:
    if isinstance(value, Mapping):
        rc = value.get("returncode", 0)
        stdout = value.get("stdout", "")
        stderr = value.get("stderr", "")
    elif isinstance(value, tuple) and len(value) == 3:
        rc, stdout, stderr = value
    else:
        rc = getattr(value, "returncode", 0)
        stdout = getattr(value, "stdout", "")
        stderr = getattr(value, "stderr", "")
    return {
        "argv": list(argv),
        "returncode": int(rc),
        "stdout": str(stdout or "")[:_OUTPUT_LIMIT],
        "stderr": str(stderr or "")[:_OUTPUT_LIMIT],
        "error": None,
    }


def _run(runner: Runner, *argv: str) -> dict[str, Any]:
    try:
        try:
            value = runner(list(argv), timeout=_TIMEOUT_SECONDS)
        except TypeError:
            # Simple test doubles often accept only argv.
            value = runner(list(argv))
        return _result(value, argv)
    except subprocess.TimeoutExpired:
        return {"argv": list(argv), "returncode": None, "stdout": "", "stderr": "", "error": "timeout"}
    except FileNotFoundError:
        return {"argv": list(argv), "returncode": None, "stdout": "", "stderr": "", "error": "not_found"}
    except PermissionError:
        return {"argv": list(argv), "returncode": None, "stdout": "", "stderr": "", "error": "permission_denied"}
    except OSError as exc:
        return {
            "argv": list(argv),
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "error": f"os_error:{exc.__class__.__name__}",
        }


def _parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"').strip("'")
    return values


def _parse_packages(text: str) -> dict[str, dict[str, str]]:
    packages: dict[str, dict[str, str]] = {}
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) >= 3:
            name, version, status = fields[:3]
            packages[name] = {"version": version, "status": status, "installed": status.startswith("ii")}
    return packages


def _parse_owners(text: str, paths: Sequence[str]) -> dict[str, str | None]:
    owners: dict[str, str | None] = {path: None for path in paths}
    for line in text.splitlines():
        if ": " not in line:
            continue
        package, path = line.split(": ", 1)
        if path in owners:
            owners[path] = package
    return owners


def _parse_csv(text: str, field_names: Sequence[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        values = [part.strip() for part in line.split(",")]
        if len(values) == len(field_names):
            rows.append(dict(zip(field_names, values)))
    return rows


def _first_line(result: Mapping[str, Any]) -> str | None:
    if result.get("returncode") != 0:
        return None
    lines = str(result.get("stdout", "")).strip().splitlines()
    return lines[0].strip() if lines else None


def _normalize_architecture(value: str | None) -> str | None:
    aliases = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    return aliases.get(str(value), str(value)) if value else None


def _parse_dkms_status(text: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    pattern = re.compile(r"^([^/\s]+)/([^,]+),\s*([^,]+),\s*([^:]+):\s*(.+)$")
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            module, version, kernel, architecture, state = match.groups()
            rows.append(
                {
                    "module": module,
                    "version": version.strip(),
                    "kernel": kernel.strip(),
                    "architecture": _normalize_architecture(architecture.strip()) or architecture.strip(),
                    "state": state.strip(),
                }
            )
    return rows


def _installed_for(
    rows: Sequence[Mapping[str, str]], module: str, version: str, kernel: str, architecture: str | None
) -> bool:
    normalized_arch = _normalize_architecture(architecture)
    return any(
        row.get("module") == module
        and row.get("version") == version
        and row.get("kernel") == kernel
        and row.get("architecture") == normalized_arch
        and str(row.get("state", "")).lower().startswith("installed")
        for row in rows
    )


def audit_host(manifest: dict[str, Any], runner: Runner | None = None) -> dict[str, Any]:
    """Inspect migration-critical host state without changing it.

    ``manifest['expected']`` describes the current host, and
    ``manifest['target']`` describes the planned driver.  A runner can be
    injected for deterministic tests; it receives an argv list and timeout.
    """

    run = runner or _default_runner
    expected = manifest.get("expected") if isinstance(manifest.get("expected"), dict) else {}
    target = manifest.get("target") if isinstance(manifest.get("target"), dict) else {}
    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    issue_codes: set[tuple[str, str]] = set()

    def issue(bucket: list[dict[str, str]], code: str, message: str) -> None:
        key = ("blocker" if bucket is blockers else "warning", code)
        if key not in issue_codes:
            bucket.append({"code": code, "message": message})
            issue_codes.add(key)

    missing = [key for key in _REQUIRED_EXPECTED if key not in expected]
    if missing:
        issue(blockers, "manifest_expected_fields_missing", "Manifest expected section lacks: " + ", ".join(missing))
    if not target.get("driver_version"):
        issue(blockers, "manifest_target_driver_missing", "Manifest target.driver_version is required.")

    os_result = _run(run, "cat", "/etc/os-release")
    os_release = _parse_os_release(os_result["stdout"]) if os_result["returncode"] == 0 else {}
    arch_result = _run(run, "dpkg", "--print-architecture")
    kernel_result = _run(run, "uname", "-r")
    observed_arch = _first_line(arch_result)
    observed_kernel = _first_line(kernel_result)
    observations: dict[str, Any] = {
        "os": {"id": os_release.get("ID"), "version_id": os_release.get("VERSION_ID"), "architecture": observed_arch},
        "kernel": {"running": observed_kernel, "expected": expected.get("kernel")},
    }
    for key, observed in (("os_id", os_release.get("ID")), ("os_version_id", os_release.get("VERSION_ID")), ("architecture", observed_arch)):
        wanted = expected.get(key)
        if observed is None:
            issue(blockers, f"{key}_unavailable", f"Could not establish host {key}.")
        elif wanted is not None and str(observed) != str(wanted):
            issue(blockers, f"{key}_mismatch", f"Observed {key} {observed!r}; expected {wanted!r}.")
    if observed_kernel is None:
        issue(blockers, "kernel_unavailable", "Could not establish the running kernel.")
    elif expected.get("kernel") and observed_kernel != expected["kernel"]:
        issue(blockers, "kernel_mismatch", f"Running kernel is {observed_kernel}; expected {expected['kernel']}.")

    header_kernel = observed_kernel or str(expected.get("kernel", ""))
    header_path = f"/usr/src/linux-headers-{header_kernel}" if header_kernel else None
    header_result = _run(run, "test", "-d", header_path) if header_path else None
    headers_present = bool(header_result and header_result["returncode"] == 0)
    observations["kernel"]["headers_path"] = header_path
    observations["kernel"]["headers_present"] = headers_present
    if not headers_present:
        issue(blockers, "kernel_headers_missing", f"Kernel headers were not found at {header_path or 'the expected path'}.")

    proc_result = _run(run, "cat", "/proc/driver/nvidia/version")
    proc_match = re.search(r"Kernel Module\s+([^\s]+)", proc_result["stdout"])
    proc_version = proc_match.group(1) if proc_match else None
    observations["driver"] = {
        "expected_version": expected.get("driver_version"),
        "target_version": target.get("driver_version"),
        "proc_version": proc_version,
        "expected_owner": expected.get("driver_owner"),
        "expected_flavor": expected.get("driver_flavor"),
    }
    if proc_version is None:
        issue(blockers, "driver_version_unavailable", "Could not read a loaded NVIDIA kernel-module version from /proc.")
    elif expected.get("driver_version") and proc_version != expected["driver_version"]:
        issue(blockers, "driver_version_mismatch", f"Loaded NVIDIA module is {proc_version}; expected {expected['driver_version']}.")

    gpu_result = _run(
        run,
        "nvidia-smi",
        "--query-gpu=uuid,index,name,driver_version,pci.bus_id",
        "--format=csv,noheader,nounits",
    )
    gpu_rows = _parse_csv(gpu_result["stdout"], ("uuid", "index", "name", "driver_version", "pci_bus_id")) if gpu_result["returncode"] == 0 else []
    expected_uuids = [str(value) for value in expected.get("gpu_uuids", [])] if isinstance(expected.get("gpu_uuids", []), list) else []
    observed_uuids = [row["uuid"] for row in gpu_rows]
    visibility_error = (gpu_result["stderr"] or gpu_result["error"] or "").strip() or None
    observations["gpu_inventory"] = {
        "visible": gpu_result["returncode"] == 0,
        "expected_count": len(expected_uuids),
        "observed_count": len(gpu_rows),
        "expected_uuids": expected_uuids,
        "observed_uuids": observed_uuids,
        "gpus": gpu_rows,
        "diagnostic": visibility_error,
    }
    if gpu_result["returncode"] != 0:
        context = "root" if os.geteuid() == 0 else "non-root"
        issue(blockers, "gpu_visibility_unavailable", f"nvidia-smi inventory is unavailable in this {context} audit; readiness cannot be established.")
        issue(warnings, "gpu_visibility_environment", "The inventory failure may reflect device or sandbox visibility; it is not evidence that physical GPUs failed.")
    elif sorted(observed_uuids) != sorted(expected_uuids):
        issue(blockers, "gpu_inventory_mismatch", f"Observed {len(observed_uuids)} GPU UUIDs; expected the manifest's {len(expected_uuids)} UUIDs.")
    observed_driver_versions = sorted({row["driver_version"] for row in gpu_rows})
    if expected.get("driver_version") and observed_driver_versions and observed_driver_versions != [expected["driver_version"]]:
        issue(blockers, "gpu_driver_version_mismatch", f"nvidia-smi reports driver versions {observed_driver_versions}; expected {expected['driver_version']}.")

    apps_result = _run(run, "nvidia-smi", "--query-compute-apps=gpu_uuid,pid,process_name", "--format=csv,noheader,nounits")
    apps = _parse_csv(apps_result["stdout"], ("gpu_uuid", "pid", "process_name")) if apps_result["returncode"] == 0 else []
    observations["activity"] = {"query_succeeded": apps_result["returncode"] == 0, "compute_apps": apps, "active_count": len(apps)}
    if apps:
        issue(warnings, "gpu_compute_activity", f"nvidia-smi reports {len(apps)} active compute process(es); quiesce them in the maintenance window.")

    cuda_result = _run(run, "readlink", "-f", "/usr/local/cuda")
    cuda_target = _first_line(cuda_result)
    trt_root = str(expected.get("tensorrt_root", ""))
    trt_result = _run(run, "test", "-d", trt_root) if trt_root else None
    observations["cuda"] = {"symlink": "/usr/local/cuda", "target": cuda_target, "expected_target": expected.get("cuda_symlink_target")}
    observations["tensorrt"] = {"root": trt_root or None, "present": bool(trt_result and trt_result["returncode"] == 0)}
    if cuda_target is None:
        issue(blockers, "cuda_symlink_unavailable", "Could not resolve /usr/local/cuda.")
    elif expected.get("cuda_symlink_target") and cuda_target != expected["cuda_symlink_target"]:
        issue(blockers, "cuda_symlink_mismatch", f"/usr/local/cuda resolves to {cuda_target}; expected {expected['cuda_symlink_target']}.")
    if not observations["tensorrt"]["present"]:
        issue(blockers, "tensorrt_root_missing", f"TensorRT root is absent at {trt_root or 'the manifest path'}.")

    current_major = str(expected.get("driver_version", "")).split(".", 1)[0]
    target_major = str(target.get("driver_version", "")).split(".", 1)[0]
    required_package_names = ["dkms", f"linux-headers-{header_kernel}", "mlnx-ofed-kernel-dkms"]
    optional_package_names: list[str] = []
    for major in (current_major, target_major):
        if major:
            optional_package_names.extend((f"nvidia-driver-{major}", f"nvidia-dkms-{major}"))
    optional_package_names = list(dict.fromkeys(optional_package_names))
    package_format = "-f=${binary:Package}\t${Version}\t${db:Status-Abbrev}\\n"
    required_packages_result = _run(run, "dpkg-query", "-W", package_format, *required_package_names)
    optional_packages_result = _run(run, "dpkg-query", "-W", package_format, *optional_package_names)
    packages = _parse_packages(required_packages_result["stdout"])
    packages.update(_parse_packages(optional_packages_result["stdout"]))
    owner_paths = [
        "/usr/bin/nvidia-smi",
        "/usr/lib/x86_64-linux-gnu/libcuda.so.1",
        f"/usr/src/nvidia-{expected.get('driver_version', '')}",
    ]
    owners_result = _run(run, "dpkg-query", "-S", *owner_paths)
    owners = _parse_owners(owners_result["stdout"], owner_paths)
    observations["packages"] = {
        "required_query": {
            "returncode": required_packages_result["returncode"],
            "diagnostic": required_packages_result["stderr"].strip(),
        },
        "optional_nvidia_query": {
            "returncode": optional_packages_result["returncode"],
            "diagnostic": optional_packages_result["stderr"].strip(),
        },
        "selected": packages,
        "path_owners": owners,
    }
    if required_packages_result["returncode"] != 0:
        issue(blockers, "baseline_package_query_failed", "The required DKMS, kernel-header, and OFED package query failed or was incomplete.")
    dkms_package = packages.get("dkms", {})
    if not dkms_package.get("installed"):
        issue(blockers, "dkms_package_missing", "The DKMS package is not reported installed.")
    elif expected.get("dkms_version") and dkms_package.get("version") != expected["dkms_version"]:
        issue(blockers, "dkms_version_mismatch", f"Installed DKMS is {dkms_package.get('version')}; expected {expected['dkms_version']}.")
    ofed_package = packages.get("mlnx-ofed-kernel-dkms", {})
    if not ofed_package.get("installed"):
        issue(blockers, "ofed_dkms_package_missing", "mlnx-ofed-kernel-dkms is not reported installed.")
    elif expected.get("ofed_package_version") and ofed_package.get("version") != expected["ofed_package_version"]:
        issue(
            blockers,
            "ofed_package_version_mismatch",
            f"Installed mlnx-ofed-kernel-dkms package is {ofed_package.get('version')}; expected {expected['ofed_package_version']}.",
        )
    core_owners = [owners[path] for path in owner_paths[:2] if owners[path]]
    observations["driver"]["dpkg_owned"] = bool(core_owners)
    observations["driver"]["dpkg_owners"] = core_owners
    if expected.get("driver_owner") == "runfile" and core_owners:
        issue(blockers, "driver_owner_mismatch", f"Manifest expects a runfile driver, but dpkg owns core NVIDIA paths: {', '.join(core_owners)}.")

    installer_result = _run(run, "head", "-n", "80", "/var/log/nvidia-installer.log")
    installer_text = installer_result["stdout"]
    observations["driver"]["runfile_log_present"] = installer_result["returncode"] == 0
    observations["driver"]["dkms_registration_in_installer_log"] = bool(re.search(r"register.*DKMS|DKMS.*register", installer_text, re.I))
    if expected.get("driver_owner") == "runfile" and installer_result["returncode"] != 0:
        issue(warnings, "runfile_log_unavailable", "Runfile ownership is expected, but /var/log/nvidia-installer.log was unavailable.")

    modules_result = _run(run, "cat", "/proc/modules")
    loaded = {line.split()[0] for line in modules_result["stdout"].splitlines() if line.split()}
    module_observations: dict[str, Any] = {"loaded": sorted(loaded.intersection({"nvidia", "nvidia_uvm", "nvidia_drm", "nvidia_modeset", "nvidia_peermem", "nv_peer_mem"}))}
    for module in ("nvidia", "nvidia_peermem"):
        details: dict[str, Any] = {"loaded": module in loaded}
        for field in ("filename", "version", "vermagic", "depends", "license"):
            details[field] = _first_line(_run(run, "modinfo", "-F", field, module))
        module_observations[module] = details
    observations["modules"] = module_observations
    nvidia_mod = module_observations["nvidia"]
    if not nvidia_mod["loaded"]:
        issue(blockers, "nvidia_module_not_loaded", "The nvidia kernel module is not listed in /proc/modules.")
    if expected.get("driver_version") and nvidia_mod["version"] != expected["driver_version"]:
        issue(blockers, "nvidia_module_version_mismatch", f"modinfo reports nvidia version {nvidia_mod['version']!r}; expected {expected['driver_version']}.")
    if expected.get("driver_flavor") == "proprietary" and str(nvidia_mod.get("license") or "").lower() not in {"nvidia", "proprietary"}:
        issue(blockers, "driver_flavor_unverified", f"modinfo license {nvidia_mod.get('license')!r} does not verify the expected proprietary flavor.")
    for module in ("nvidia", "nvidia_peermem"):
        details = module_observations[module]
        filename = str(details.get("filename") or "")
        if not filename.startswith((f"/lib/modules/{header_kernel}/", f"/usr/lib/modules/{header_kernel}/")):
            issue(blockers, f"{module}_path_mismatch", f"{module} resolves outside the running kernel module tree: {filename!r}.")
    nvidia_vermagic_kernel = str(nvidia_mod.get("vermagic") or "").split()[:1]
    if nvidia_vermagic_kernel != [observed_kernel]:
        issue(blockers, "nvidia_vermagic_mismatch", "nvidia module vermagic does not match the running kernel.")
    peermem_mod = module_observations["nvidia_peermem"]
    if not peermem_mod["loaded"]:
        issue(blockers, "nvidia_peermem_not_loaded", "nvidia_peermem is not loaded; the OFED/GPUDirect bridge cannot be verified.")
    if expected.get("driver_version") and peermem_mod["version"] != expected["driver_version"]:
        issue(blockers, "nvidia_peermem_version_mismatch", f"modinfo reports nvidia_peermem version {peermem_mod['version']!r}; expected driver {expected['driver_version']}.")
    peermem_vermagic_kernel = next(iter(str(peermem_mod.get("vermagic") or "").split()), None)
    if observed_kernel and peermem_vermagic_kernel != observed_kernel:
        issue(blockers, "nvidia_peermem_vermagic_mismatch", f"nvidia_peermem vermagic targets {peermem_vermagic_kernel!r}; running kernel is {observed_kernel}.")
    peermem_depends = {item.strip() for item in str(peermem_mod.get("depends") or "").split(",") if item.strip()}
    if "ib_core" not in peermem_depends:
        issue(blockers, "nvidia_peermem_ib_core_missing", "nvidia_peermem does not report its required ib_core dependency.")

    global_dkms = _run(run, "dkms", "status")
    nvidia_dkms = _run(run, "dkms", "status", "-m", "nvidia")
    nvidia_version_dkms = _run(run, "dkms", "status", "-m", "nvidia", "-v", str(expected.get("driver_version", "")))
    ofed_version = str(expected.get("ofed_dkms_version", ""))
    ofed_version_dkms = _run(run, "dkms", "status", "-m", "mlnx-ofed-kernel", "-v", ofed_version)
    nvidia_fs_dkms = _run(run, "dkms", "status", "-m", "nvidia-fs")
    registered_result = _run(run, "ls", "-1", "/var/lib/dkms/nvidia")
    registered_versions = [line.strip() for line in registered_result["stdout"].splitlines() if re.fullmatch(r"[0-9][0-9A-Za-z._+-]*", line.strip())]
    global_text = global_dkms["stdout"] + "\n" + global_dkms["stderr"]
    fs_text = global_text + "\n" + nvidia_fs_dkms["stdout"] + "\n" + nvidia_fs_dkms["stderr"]
    stale_fs = "nvidia-fs" in fs_text.lower() and bool(re.search(r"dkms\.conf|does not exist|missing|broken|error", fs_text, re.I))
    observations["dkms"] = {
        "global": {"returncode": global_dkms["returncode"], "diagnostic": global_text.strip()[:_OUTPUT_LIMIT]},
        "nvidia": {"returncode": nvidia_dkms["returncode"], "status": nvidia_dkms["stdout"].strip()},
        "nvidia_expected_version": {"returncode": nvidia_version_dkms["returncode"], "status": nvidia_version_dkms["stdout"].strip()},
        "ofed_expected_version": {"returncode": ofed_version_dkms["returncode"], "status": ofed_version_dkms["stdout"].strip()},
        "nvidia_fs": {"returncode": nvidia_fs_dkms["returncode"], "diagnostic": (nvidia_fs_dkms["stdout"] + "\n" + nvidia_fs_dkms["stderr"]).strip()},
        "registered_nvidia_versions": sorted(registered_versions),
        "stale_nvidia_fs": stale_fs,
    }
    expected_driver = str(expected.get("driver_version", ""))
    dkms_architecture = observed_arch or str(expected.get("architecture", ""))
    nvidia_rows = _parse_dkms_status(nvidia_version_dkms["stdout"])
    ofed_rows = _parse_dkms_status(ofed_version_dkms["stdout"])
    observations["dkms"]["nvidia_expected_version"]["rows"] = nvidia_rows
    observations["dkms"]["ofed_expected_version"]["rows"] = ofed_rows
    if nvidia_version_dkms["returncode"] != 0 or not _installed_for(
        nvidia_rows, "nvidia", expected_driver, header_kernel, dkms_architecture
    ):
        issue(blockers, "nvidia_dkms_registration_missing", "Expected NVIDIA driver is not reported installed for the running kernel and architecture by targeted DKMS status.")
    if global_dkms["returncode"] != 0:
        issue(blockers, "dkms_diagnostic_failed", "Global DKMS status failed; module readiness cannot be established.")
    elif not stale_fs and (global_dkms["stderr"].strip() or re.search(r"error|broken|missing", global_dkms["stdout"], re.I)):
        issue(blockers, "dkms_diagnostic_failed", "Global DKMS status contains diagnostics despite a successful exit status.")
    if stale_fs:
        issue(blockers, "stale_nvidia_fs_dkms", "DKMS reports a stale nvidia-fs registration or missing dkms.conf; resolve it before driver or kernel maintenance.")
    if ofed_version and (
        ofed_version_dkms["returncode"] != 0
        or not _installed_for(ofed_rows, "mlnx-ofed-kernel", ofed_version, header_kernel, dkms_architecture)
    ):
        issue(blockers, "ofed_dkms_registration_missing", "Expected MLNX_OFED module is not reported installed for the running kernel and architecture by targeted DKMS status.")

    target_owner = target.get("driver_owner", "apt")
    if expected.get("driver_owner") == "runfile" and target_owner == "apt":
        issue(warnings, "runfile_uninstall_required", "The current runfile driver must be cleanly uninstalled before an APT-managed NVIDIA driver is installed; this audit does not perform cleanup.")
    if len(registered_versions) > 1:
        issue(blockers, "multiple_nvidia_dkms_versions", f"Multiple NVIDIA DKMS source versions are registered: {', '.join(sorted(registered_versions))}.")
    if registered_result["returncode"] != 0:
        issue(blockers, "nvidia_dkms_versions_unavailable", "Could not inspect registered NVIDIA DKMS source versions.")
    elif expected_driver and expected_driver not in registered_versions:
        issue(blockers, "nvidia_dkms_source_missing", f"Expected NVIDIA DKMS source version {expected_driver} is not registered.")

    observations["readiness"] = {
        "ready": not blockers,
        "scope": "host_preflight_evidence_only",
        "installation_ready": False,
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
    }
    return {"observations": observations, "blockers": blockers, "warnings": warnings}
