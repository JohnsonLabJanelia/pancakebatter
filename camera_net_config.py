#!/usr/bin/env python3

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from check_network_settings import (
    EXPECTED_SCHEMA_NAME,
    EXPECTED_SCHEMA_VERSION,
    config_bool,
    load_config,
    normalize_mac,
    parse_camera_configs,
    parse_nic_configs,
)


DEFAULT_EVTTOOLS = Path("/opt/EVT/eSDK/tools/evttools")


class CameraNetConfigError(Exception):
    """Raised when a camera network move cannot be planned or applied."""


def default_config_path() -> Path:
    return Path(f"{socket.gethostname().split('.')[0]}_config.yml")


def resolve_config_path(config_path: str | None) -> Path:
    path = Path(config_path) if config_path else default_config_path()
    if path.is_absolute() or path.exists():
        return path
    return Path(__file__).resolve().parent / path


def camera_label(mac: str, camera: dict[str, Any]) -> str:
    serial = camera.get("serial_number", "unknown")
    return f"{mac} serial={serial}"


def find_camera(camera_configs: dict[str, dict[str, Any]], camera_id: str) -> tuple[str, dict[str, Any]]:
    wanted_mac = normalize_mac(camera_id)
    wanted_serial = str(camera_id).strip()
    matches: list[tuple[str, dict[str, Any]]] = []

    for mac, camera in camera_configs.items():
        if normalize_mac(mac) == wanted_mac or str(camera.get("serial_number", "")).strip() == wanted_serial:
            matches.append((mac, camera))

    if not matches:
        raise CameraNetConfigError(f"camera {camera_id!r} was not found by MAC or serial_number")
    if len(matches) > 1:
        labels = ", ".join(camera_label(mac, camera) for mac, camera in matches)
        raise CameraNetConfigError(f"camera {camera_id!r} matched multiple entries: {labels}")
    return matches[0]


def get_schema_version(config: dict[str, Any]) -> tuple[str | None, int | None]:
    schema = config.get("schema", {})
    if not isinstance(schema, dict):
        return None, None
    return schema.get("name"), schema.get("version")


def require_system_config_v1(config: dict[str, Any]) -> None:
    name, version = get_schema_version(config)
    if name != EXPECTED_SCHEMA_NAME or version != EXPECTED_SCHEMA_VERSION:
        raise CameraNetConfigError(
            f"expected {EXPECTED_SCHEMA_NAME} v{EXPECTED_SCHEMA_VERSION}, found name={name!r} version={version!r}"
        )


def get_nic_interface(nic_name: str, nic_config: dict[str, Any]) -> ipaddress.IPv4Interface:
    try:
        return ipaddress.ip_interface(str(nic_config["ip_address"]))
    except KeyError as exc:
        raise CameraNetConfigError(f"{nic_name}: missing ip_address") from exc
    except ValueError as exc:
        raise CameraNetConfigError(f"{nic_name}: invalid ip_address {nic_config.get('ip_address')!r}: {exc}") from exc


def infer_camera_ip(nic_interface: ipaddress.IPv4Interface) -> ipaddress.IPv4Address:
    candidate = ipaddress.ip_address(int(nic_interface.ip) + 1)
    if candidate not in nic_interface.network:
        raise CameraNetConfigError(f"could not infer camera IP from host NIC IP {nic_interface}")
    return candidate


def cidr_to_netmask(nic_interface: ipaddress.IPv4Interface) -> str:
    return str(nic_interface.network.netmask)


def check_ip_conflicts(
    camera_configs: dict[str, dict[str, Any]],
    target_camera_mac: str,
    target_ip: ipaddress.IPv4Address,
    target_nic: str,
) -> None:
    for mac, camera in camera_configs.items():
        if normalize_mac(mac) == normalize_mac(target_camera_mac):
            continue
        if str(camera.get("ip_address")) == str(target_ip):
            raise CameraNetConfigError(f"{target_ip} is already assigned to {camera_label(mac, camera)}")
        if camera.get("nic_port") == target_nic:
            raise CameraNetConfigError(f"{target_nic} is already assigned to {camera_label(mac, camera)}")


def transceiver_label(transceiver: dict[str, Any] | None) -> str:
    if not isinstance(transceiver, dict):
        return "not recorded"
    serial = transceiver.get("serial_number")
    brand = transceiver.get("brand")
    model = transceiver.get("model")
    if serial:
        return f"{serial} ({brand or 'unknown'} {model or 'unknown'})"
    if brand or model:
        return f"unserialized ({brand or 'unknown'} {model or 'unknown'})"
    return "empty"


def require_evttools_compatible_ip(target_ip: ipaddress.IPv4Address, nic_interface: ipaddress.IPv4Interface) -> None:
    first_evttools_ip = infer_camera_ip(nic_interface)
    if target_ip != first_evttools_ip:
        raise CameraNetConfigError(
            "evttools v1 can only assign the first camera IP on a port. "
            f"For {nic_interface.ip}, that is {first_evttools_ip}, not {target_ip}."
        )


def build_evttools_command(evttools: Path, nic_interface: ipaddress.IPv4Interface) -> list[str]:
    return [str(evttools), "-f", str(nic_interface.ip), "-o", "p"]


def run_command(args: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(args, 127, "", f"command not found: {exc.filename}")
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        if not stderr:
            stderr = f"command timed out after {timeout}s: {' '.join(args)}"
        return subprocess.CompletedProcess(args, 124, stdout, stderr)


def combined_command_output(result: subprocess.CompletedProcess[str]) -> str:
    parts = [part.strip() for part in (result.stdout, result.stderr) if part and part.strip()]
    return "\n".join(parts)


def verify_camera_reachable(interface: str, camera_ip: ipaddress.IPv4Address, timeout: int) -> None:
    command = ["arping", "-c", "2", "-I", interface, str(camera_ip)]
    print("\nVerifying programmed camera is reachable before updating config...")
    print("  " + " ".join(command))
    result = run_command(command, timeout=timeout)
    if result.returncode == 0:
        print(f"OK: {camera_ip} responded on {interface}")
        return

    output = combined_command_output(result)
    detail = f"\n{output}" if output else ""
    raise CameraNetConfigError(
        f"{camera_ip} did not respond on {interface} after camera programming; config was not updated.{detail}"
    )


def find_yaml_mapping_block(lines: list[str], key: str, start_index: int, parent_indent: int) -> tuple[int, int, int]:
    key_re = re.compile(rf"^(\s*){re.escape(key)}:\s*(?:#.*)?$")
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent <= parent_indent:
            break

        match = key_re.match(line.rstrip("\n"))
        if not match:
            continue

        key_indent = len(match.group(1))
        end = index + 1
        while end < len(lines):
            next_line = lines[end]
            if next_line.strip() and not next_line.lstrip().startswith("#"):
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                if next_indent <= key_indent:
                    break
            end += 1
        return index, end, key_indent

    raise CameraNetConfigError(f"could not locate YAML mapping for camera {key}")


def find_root_mapping(lines: list[str], key: str) -> tuple[int, int, int]:
    key_re = re.compile(rf"^(\s*){re.escape(key)}:\s*(?:#.*)?$")
    for index, line in enumerate(lines):
        match = key_re.match(line.rstrip("\n"))
        if not match:
            continue
        indent = len(match.group(1))
        end = index + 1
        while end < len(lines):
            next_line = lines[end]
            if next_line.strip() and not next_line.lstrip().startswith("#"):
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                if next_indent <= indent:
                    break
            end += 1
        return index, end, indent
    raise CameraNetConfigError(f"could not locate root YAML mapping {key}")


def find_yaml_sequence_mapping_block(
    lines: list[str],
    key: str,
    start_index: int,
    parent_indent: int,
) -> tuple[int, int, int]:
    key_re = re.compile(rf"^(\s*)-\s+{re.escape(key)}:\s*(?:#.*)?$")
    for index in range(start_index, len(lines)):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if indent <= parent_indent:
            break

        match = key_re.match(line.rstrip("\n"))
        if not match:
            continue

        item_indent = len(match.group(1))
        end = index + 1
        while end < len(lines):
            next_line = lines[end]
            if next_line.strip() and not next_line.lstrip().startswith("#"):
                next_indent = len(next_line) - len(next_line.lstrip(" "))
                if next_indent <= item_indent:
                    break
            end += 1
        return index, end, item_indent

    raise CameraNetConfigError(f"could not locate YAML sequence mapping for NIC {key}")


def replace_or_insert_child(lines: list[str], start: int, end: int, parent_indent: int, key: str, value: str) -> None:
    child_re = re.compile(rf"^(\s*){re.escape(key)}\s*:\s*.*$")
    for index in range(start + 1, end):
        match = child_re.match(lines[index].rstrip("\n"))
        if not match:
            continue
        indent = len(match.group(1))
        if indent <= parent_indent:
            continue
        newline = "\n" if lines[index].endswith("\n") else ""
        lines[index] = f"{match.group(1)}{key}: {value}{newline}"
        return

    insert_at = end
    child_indent = " " * (parent_indent + 2)
    lines.insert(insert_at, f"{child_indent}{key}: {value}\n")


def replace_block(lines: list[str], start: int, end: int, replacement: list[str]) -> int:
    lines[start:end] = replacement
    return start + len(replacement)


def null_transceiver_block(transceiver_block: list[str]) -> list[str]:
    null_block = [transceiver_block[0]]
    child_re = re.compile(r"^(\s*)([^#:\s][^:]*):.*(\n?)$")
    for line in transceiver_block[1:]:
        match = child_re.match(line)
        if not match:
            null_block.append(line)
            continue
        null_block.append(f"{match.group(1)}{match.group(2)}: null{match.group(3)}")
    return null_block


def get_nic_block(lines: list[str], nic_name: str) -> tuple[int, int, int]:
    nics_start, _nics_end, nics_indent = find_root_mapping(lines, "nics")
    return find_yaml_sequence_mapping_block(lines, nic_name, nics_start + 1, nics_indent)


def get_transceiver_block(lines: list[str], nic_name: str) -> tuple[int, int, int]:
    nic_start, nic_end, nic_indent = get_nic_block(lines, nic_name)
    return find_yaml_mapping_block(lines, "transceiver", nic_start + 1, nic_indent)


def apply_transceiver_update(lines: list[str], old_nic: str, new_nic: str, mode: str) -> None:
    if old_nic == new_nic:
        raise CameraNetConfigError("NIC transceiver move/swap requires a different target NIC")

    old_start, old_end, _old_indent = get_transceiver_block(lines, old_nic)
    new_start, new_end, _new_indent = get_transceiver_block(lines, new_nic)
    old_block = list(lines[old_start:old_end])
    new_block = list(lines[new_start:new_end])

    replacements: list[tuple[int, int, list[str]]] = []
    if mode == "move":
        replacements = [
            (old_start, old_end, null_transceiver_block(old_block)),
            (new_start, new_end, old_block),
        ]
    elif mode == "swap":
        replacements = [
            (old_start, old_end, new_block),
            (new_start, new_end, old_block),
        ]
    else:
        return

    for start, end, replacement in sorted(replacements, key=lambda item: item[0], reverse=True):
        replace_block(lines, start, end, replacement)


def apply_nic_lifecycle_update(
    lines: list[str],
    nic_name: str,
    role: str,
    managed: bool,
    expected_link: bool,
) -> None:
    nic_start, nic_end, nic_indent = get_nic_block(lines, nic_name)
    replace_or_insert_child(lines, nic_start, nic_end, nic_indent, "role", role)
    nic_start, nic_end, nic_indent = get_nic_block(lines, nic_name)
    replace_or_insert_child(lines, nic_start, nic_end, nic_indent, "managed", str(managed).lower())
    nic_start, nic_end, nic_indent = get_nic_block(lines, nic_name)
    replace_or_insert_child(lines, nic_start, nic_end, nic_indent, "expected_link", str(expected_link).lower())


def validate_transceiver_update(config_path: Path, old_nic: str, new_nic: str, mode: str) -> None:
    if mode == "none":
        return
    if old_nic == new_nic:
        raise CameraNetConfigError("NIC transceiver move/swap requires a different target NIC")

    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    get_transceiver_block(lines, old_nic)
    get_transceiver_block(lines, new_nic)


def update_config_yaml(
    config_path: Path,
    camera_mac: str,
    old_nic: str,
    new_ip: str,
    new_nic: str,
    transceiver_mode: str,
    retire_old_nic: bool,
) -> bool:
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    cameras_start, _cameras_end, cameras_indent = find_root_mapping(lines, "cameras")
    camera_start, camera_end, camera_indent = find_yaml_mapping_block(
        lines,
        camera_mac,
        cameras_start + 1,
        cameras_indent,
    )

    original = list(lines)
    replace_or_insert_child(lines, camera_start, camera_end, camera_indent, "ip_address", new_ip)
    replace_or_insert_child(lines, camera_start, camera_end, camera_indent, "nic_port", new_nic)
    apply_transceiver_update(lines, old_nic, new_nic, transceiver_mode)
    if retire_old_nic and old_nic != new_nic:
        apply_nic_lifecycle_update(lines, old_nic, role="spare", managed=False, expected_link=False)

    if lines == original:
        return False

    config_path.write_text("".join(lines), encoding="utf-8")
    return True


def update_nic_activation_yaml(
    config_path: Path,
    nic_name: str,
    role: str,
    expected_link: bool,
    autoneg: str,
) -> bool:
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    original = list(lines)

    apply_nic_lifecycle_update(lines, nic_name, role=role, managed=True, expected_link=expected_link)

    if autoneg != "keep":
        nic_start, _nic_end, nic_indent = get_nic_block(lines, nic_name)
        link_start, link_end, link_indent = find_yaml_mapping_block(lines, "link_settings", nic_start + 1, nic_indent)
        replace_or_insert_child(lines, link_start, link_end, link_indent, "autoneg", autoneg)

    if lines == original:
        return False

    config_path.write_text("".join(lines), encoding="utf-8")
    return True


def update_nic_deactivation_yaml(config_path: Path, nic_name: str, role: str) -> bool:
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    original = list(lines)

    apply_nic_lifecycle_update(lines, nic_name, role=role, managed=False, expected_link=False)

    if lines == original:
        return False

    config_path.write_text("".join(lines), encoding="utf-8")
    return True


def backup_config(config_path: Path) -> Path:
    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = config_path.with_name(f"{config_path.name}.bak.{timestamp}")
    shutil.copy2(config_path, backup_path)
    return backup_path


def run_post_check(config_path: Path, skip_camera_arping: bool, verbose: bool) -> int:
    checker = Path(__file__).resolve().parent / "check_network_settings.py"
    args = [sys.executable, "-B", str(checker), "--config", str(config_path)]
    if verbose:
        args.append("--verbose")
    if skip_camera_arping:
        args.append("--skip-camera-arping")

    print("\nRunning post-check:")
    print("  " + " ".join(args))
    result = run_command(args, timeout=60)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def print_plan(
    config_path: Path,
    camera_mac: str,
    camera: dict[str, Any],
    target_nic: str,
    target_ip: ipaddress.IPv4Address,
    nic_interface: ipaddress.IPv4Interface,
    evttools_command: list[str] | None,
    apply: bool,
    program_camera: bool,
    old_nic_transceiver: str,
    target_nic_transceiver: str,
    transceiver_mode: str,
    retire_old_nic: bool,
) -> None:
    print("Camera network move plan")
    print(f"  config: {config_path}")
    print(f"  camera: {camera_label(camera_mac, camera)}")
    print(f"  old nic_port: {camera.get('nic_port')}")
    print(f"  old ip_address: {camera.get('ip_address')}")
    print(f"  new nic_port: {target_nic}")
    print(f"  new ip_address: {target_ip}")
    print(f"  target host NIC IP: {nic_interface}")
    print(f"  subnet mask: {cidr_to_netmask(nic_interface)}")
    print(f"  mode: {'apply' if apply else 'dry-run'}")
    print(f"  camera programming: {'enabled' if program_camera else 'disabled'}")
    print(f"  old NIC transceiver: {old_nic_transceiver}")
    print(f"  target NIC transceiver: {target_nic_transceiver}")
    if transceiver_mode == "move":
        print("  NIC transceiver action: move old NIC transceiver to target; old NIC becomes empty")
    elif transceiver_mode == "swap":
        print("  NIC transceiver action: swap old and target NIC transceivers")
    else:
        print("  NIC transceiver action: unchanged")
    if retire_old_nic and camera.get("nic_port") != target_nic:
        print("  old NIC lifecycle action: mark old NIC spare, managed=false, expected_link=false")
    else:
        print("  old NIC lifecycle action: unchanged")
    if evttools_command:
        print("  evttools command:")
        print("    " + " ".join(evttools_command))


def print_activation_plan(
    config_path: Path,
    nic_name: str,
    nic_config: dict[str, Any],
    role: str,
    expected_link: bool,
    autoneg: str,
    apply: bool,
) -> None:
    link_settings = nic_config.get("link_settings", {}) if isinstance(nic_config.get("link_settings"), dict) else {}
    print("NIC activation plan")
    print(f"  config: {config_path}")
    print(f"  nic: {nic_name}")
    print(f"  ip_address: {nic_config.get('ip_address')}")
    print(f"  current role: {nic_config.get('role')}")
    print(f"  current managed: {nic_config.get('managed')}")
    print(f"  current expected_link: {nic_config.get('expected_link')}")
    print(f"  current autoneg: {link_settings.get('autoneg')}")
    print(f"  new role: {role}")
    print("  new managed: true")
    print(f"  new expected_link: {str(expected_link).lower()}")
    print(f"  new autoneg: {autoneg}")
    print(f"  mode: {'apply' if apply else 'dry-run'}")


def print_deactivation_plan(
    config_path: Path,
    nic_name: str,
    nic_config: dict[str, Any],
    role: str,
    apply: bool,
) -> None:
    print("NIC deactivation plan")
    print(f"  config: {config_path}")
    print(f"  nic: {nic_name}")
    print(f"  ip_address: {nic_config.get('ip_address')}")
    print(f"  current role: {nic_config.get('role')}")
    print(f"  current managed: {nic_config.get('managed')}")
    print(f"  current expected_link: {nic_config.get('expected_link')}")
    print(f"  new role: {role}")
    print("  new managed: false")
    print("  new expected_link: false")
    print(f"  mode: {'apply' if apply else 'dry-run'}")


def apply_move(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    config = load_config(str(config_path))
    require_system_config_v1(config)

    nic_configs = parse_nic_configs(config)
    camera_configs = parse_camera_configs(config)
    camera_mac, camera = find_camera(camera_configs, args.camera)

    if args.nic not in nic_configs:
        raise CameraNetConfigError(f"target NIC {args.nic!r} is not defined in nics")

    target_nic_config = nic_configs[args.nic]
    if not config_bool(target_nic_config.get("managed"), True):
        raise CameraNetConfigError(f"target NIC {args.nic} has managed=false; activate it before assigning a camera")

    old_nic = str(camera.get("nic_port"))
    transceiver_mode = "none"
    if args.move_nic_transceiver:
        transceiver_mode = "move"
    elif args.swap_nic_transceivers:
        transceiver_mode = "swap"

    if transceiver_mode != "none" or args.retire_old_nic:
        if old_nic not in nic_configs:
            raise CameraNetConfigError(f"old camera NIC {old_nic!r} is not defined in nics")
    if transceiver_mode != "none":
        validate_transceiver_update(config_path, old_nic, args.nic, transceiver_mode)

    nic_interface = get_nic_interface(args.nic, target_nic_config)
    target_ip = ipaddress.ip_address(args.ip) if args.ip else infer_camera_ip(nic_interface)
    if target_ip not in nic_interface.network:
        raise CameraNetConfigError(f"{target_ip} is outside target NIC subnet {nic_interface.network}")
    if target_ip == nic_interface.ip:
        raise CameraNetConfigError(f"{target_ip} is the host NIC IP for {args.nic}")

    check_ip_conflicts(camera_configs, camera_mac, target_ip, args.nic)

    program_camera = not args.no_program_camera
    evttools_command: list[str] | None = None
    if program_camera:
        evttools = Path(args.evttools)
        if not evttools.exists():
            raise CameraNetConfigError(f"evttools not found at {evttools}")
        require_evttools_compatible_ip(target_ip, nic_interface)
        evttools_command = build_evttools_command(evttools, nic_interface)

    print_plan(
        config_path=config_path,
        camera_mac=camera_mac,
        camera=camera,
        target_nic=args.nic,
        target_ip=target_ip,
        nic_interface=nic_interface,
        evttools_command=evttools_command,
        apply=args.apply,
        program_camera=program_camera,
        old_nic_transceiver=transceiver_label(nic_configs.get(old_nic, {}).get("transceiver")),
        target_nic_transceiver=transceiver_label(target_nic_config.get("transceiver")),
        transceiver_mode=transceiver_mode,
        retire_old_nic=args.retire_old_nic,
    )

    if not args.apply:
        print("\nDry run only. Re-run with --apply to program the camera and update the config.")
        return 0

    if program_camera and os.geteuid() != 0:
        raise CameraNetConfigError("camera programming requires root; rerun with sudo or use --no-program-camera")

    if program_camera and evttools_command:
        print("\nProgramming camera IP persistently with evttools...")
        result = run_command(evttools_command, timeout=args.evttools_timeout)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        if result.returncode != 0:
            raise CameraNetConfigError(f"evttools failed with exit code {result.returncode}; config was not updated")
        if args.settle_seconds > 0:
            time.sleep(args.settle_seconds)
        if not args.skip_camera_program_verify:
            verify_camera_reachable(args.nic, target_ip, timeout=args.program_verify_timeout)

    backup_path = backup_config(config_path)
    changed = update_config_yaml(
        config_path,
        camera_mac,
        old_nic,
        str(target_ip),
        args.nic,
        transceiver_mode,
        args.retire_old_nic,
    )
    if changed:
        print(f"\nUpdated {config_path}")
        print(f"Backup: {backup_path}")
    else:
        print(f"\nConfig already matched requested assignment. Backup: {backup_path}")

    if not args.skip_post_check:
        return run_post_check(config_path, skip_camera_arping=args.skip_camera_arping, verbose=args.verbose)
    return 0


def apply_activate_nic(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    config = load_config(str(config_path))
    require_system_config_v1(config)

    nic_configs = parse_nic_configs(config)
    if args.nic not in nic_configs:
        raise CameraNetConfigError(f"NIC {args.nic!r} is not defined in nics")

    nic_config = nic_configs[args.nic]
    get_nic_interface(args.nic, nic_config)
    if not isinstance(nic_config.get("link_settings"), dict):
        raise CameraNetConfigError(f"{args.nic}: missing link_settings")

    print_activation_plan(
        config_path=config_path,
        nic_name=args.nic,
        nic_config=nic_config,
        role=args.role,
        expected_link=args.expected_link,
        autoneg=args.autoneg,
        apply=args.apply,
    )

    if not args.apply:
        print("\nDry run only. Re-run with --apply to update the config.")
        return 0

    backup_path = backup_config(config_path)
    changed = update_nic_activation_yaml(
        config_path=config_path,
        nic_name=args.nic,
        role=args.role,
        expected_link=args.expected_link,
        autoneg=args.autoneg,
    )

    if changed:
        print(f"\nUpdated {config_path}")
        print(f"Backup: {backup_path}")
    else:
        print(f"\nConfig already matched requested activation. Backup: {backup_path}")

    print("\nNext host-side steps:")
    print("  sudo ./create_nm_connections.sh")
    print("  sudo ./configure_interfaces.sh")
    print("\nAfter the physical link is connected and the camera move is applied:")
    print("  sudo ./check_system.sh --only network --verbose")
    return 0


def apply_deactivate_nic(args: argparse.Namespace) -> int:
    config_path = resolve_config_path(args.config)
    config = load_config(str(config_path))
    require_system_config_v1(config)

    nic_configs = parse_nic_configs(config)
    if args.nic not in nic_configs:
        raise CameraNetConfigError(f"NIC {args.nic!r} is not defined in nics")

    nic_config = nic_configs[args.nic]
    get_nic_interface(args.nic, nic_config)

    print_deactivation_plan(
        config_path=config_path,
        nic_name=args.nic,
        nic_config=nic_config,
        role=args.role,
        apply=args.apply,
    )

    if not args.apply:
        print("\nDry run only. Re-run with --apply to update the config.")
        return 0

    backup_path = backup_config(config_path)
    changed = update_nic_deactivation_yaml(
        config_path=config_path,
        nic_name=args.nic,
        role=args.role,
    )

    if changed:
        print(f"\nUpdated {config_path}")
        print(f"Backup: {backup_path}")
    else:
        print(f"\nConfig already matched requested deactivation. Backup: {backup_path}")

    print("\nNext host-side steps:")
    print(f"  sudo nmcli device disconnect {args.nic}")
    print("  sudo ./check_system.sh --only network --verbose")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Move a configured Emergent camera to a NIC/IP assignment.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    move = subparsers.add_parser("move", help="Plan or apply a camera NIC/IP assignment change.")
    move.add_argument("--camera", required=True, help="Camera serial_number or MAC address.")
    move.add_argument("--nic", required=True, help="Target NIC name from system_config v1 nics.")
    move.add_argument("--ip", help="Target camera IP. Defaults to target host NIC IP + 1.")
    move.add_argument("--config", help="Config path. Defaults to ./$(hostname -s)_config.yml.")
    move.add_argument("--apply", action="store_true", help="Apply the camera programming and YAML update.")
    move.add_argument(
        "--no-program-camera",
        action="store_true",
        help="Only update YAML. Use when the camera has already been programmed.",
    )
    transceiver_group = move.add_mutually_exclusive_group()
    transceiver_group.add_argument(
        "--move-nic-transceiver",
        action="store_true",
        help="Move the old NIC transceiver block to the target NIC and clear the old NIC transceiver.",
    )
    transceiver_group.add_argument(
        "--swap-nic-transceivers",
        action="store_true",
        help="Swap the old and target NIC transceiver blocks.",
    )
    move.add_argument(
        "--retire-old-nic",
        action="store_true",
        help="Mark the previous NIC as spare, managed=false, expected_link=false after the move.",
    )
    move.add_argument("--evttools", default=str(DEFAULT_EVTTOOLS), help="Path to EVT evttools binary.")
    move.add_argument("--evttools-timeout", type=int, default=60, help="Seconds to wait for evttools.")
    move.add_argument("--settle-seconds", type=float, default=2.0, help="Delay after evttools before updating config.")
    move.add_argument(
        "--program-verify-timeout",
        type=int,
        default=10,
        help="Seconds to wait for the post-evttools arping verification before updating config.",
    )
    move.add_argument(
        "--skip-camera-program-verify",
        action="store_true",
        help="Do not verify the target camera IP responds after evttools and before updating config.",
    )
    move.add_argument("--skip-post-check", action="store_true", help="Do not run check_network_settings.py after update.")
    move.add_argument("--skip-camera-arping", action="store_true", help="Skip camera ARP probe in post-check.")
    move.add_argument("--verbose", action="store_true", help="Pass verbose output to the post-check.")
    move.set_defaults(func=apply_move)

    activate = subparsers.add_parser("activate-nic", help="Mark a configured NIC as managed/active in the host config.")
    activate.add_argument("--nic", required=True, help="NIC name from system_config v1 nics.")
    activate.add_argument("--config", help="Config path. Defaults to ./$(hostname -s)_config.yml.")
    activate.add_argument("--role", default="camera", choices=["camera", "spare", "management", "uplink", "unknown"])
    expected_link = activate.add_mutually_exclusive_group()
    expected_link.add_argument(
        "--expected-link",
        dest="expected_link",
        action="store_true",
        help="Mark the NIC as expecting physical carrier.",
    )
    expected_link.add_argument(
        "--no-expected-link",
        dest="expected_link",
        action="store_false",
        help="Mark the NIC as not expecting physical carrier.",
    )
    activate.set_defaults(expected_link=True)
    activate.add_argument("--autoneg", default="true", choices=["true", "false", "keep"])
    activate.add_argument("--apply", action="store_true", help="Apply the config update.")
    activate.set_defaults(func=apply_activate_nic)

    deactivate = subparsers.add_parser(
        "deactivate-nic",
        help="Mark a configured NIC as spare/unmanaged in the host config.",
    )
    deactivate.add_argument("--nic", required=True, help="NIC name from system_config v1 nics.")
    deactivate.add_argument("--config", help="Config path. Defaults to ./$(hostname -s)_config.yml.")
    deactivate.add_argument("--role", default="spare", choices=["camera", "spare", "management", "uplink", "unknown"])
    deactivate.add_argument("--apply", action="store_true", help="Apply the config update.")
    deactivate.set_defaults(func=apply_deactivate_nic)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        return args.func(args)
    except CameraNetConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
