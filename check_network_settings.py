#!/usr/bin/env python3

import argparse
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any

import yaml

import hostconfig


EXPECTED_SCHEMA_NAME = "system_config"
EXPECTED_SCHEMA_VERSION = 1
NIC_ROLES = {"camera", "spare", "management", "uplink", "unknown"}


@dataclass
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class Reporter:
    verbose: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def section(self, title: str) -> None:
        print(f"\n== {title} ==", flush=True)

    def ok(self, message: str) -> None:
        print(f"OK: {message}", flush=True)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"WARN: {message}", file=sys.stderr, flush=True)

    def error(self, message: str) -> None:
        self.errors.append(message)
        print(f"ERROR: {message}", file=sys.stderr, flush=True)

    def debug(self, message: str) -> None:
        if self.verbose:
            print(f"DEBUG: {message}", flush=True)


def run_command(args: list[str], timeout: int = 10) -> CommandResult:
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return CommandResult(result.returncode, result.stdout, result.stderr)
    except FileNotFoundError:
        return CommandResult(127, "", f"command not found: {args[0]}")
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            124,
            exc.stdout or "",
            exc.stderr or f"command timed out after {timeout}s: {' '.join(args)}",
        )


def default_config_path() -> str:
    return str(hostconfig.default_config_path())


def resolve_config_path(config_path: str) -> str:
    if os.path.isabs(config_path):
        return config_path
    if os.path.exists(config_path):
        return config_path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, config_path)


def load_config(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)
    if not isinstance(config, dict):
        raise ValueError(f"{path} did not parse as a YAML mapping")
    return config


def normalize_mac(mac: str | None) -> str:
    if not mac:
        return ""
    return re.sub(r"[^0-9a-fA-F]", "", mac).lower()


def normalize_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    value_str = str(value).strip().lower()
    if value_str in {"true", "yes", "on", "1"}:
        return True
    if value_str in {"false", "no", "off", "0"}:
        return False
    return None


def config_bool(value: Any, default: bool) -> bool:
    parsed = normalize_bool(value)
    return default if parsed is None else parsed


def parse_nic_configs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    nic_configs: dict[str, dict[str, Any]] = {}
    raw_nics = config.get("nics", [])
    if not isinstance(raw_nics, list):
        raise ValueError("config key 'nics' must be a list")

    for index, item in enumerate(raw_nics):
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError(f"nics[{index}] must be a one-entry mapping")
        nic_name, nic_config = next(iter(item.items()))
        if not isinstance(nic_config, dict):
            raise ValueError(f"nics[{index}].{nic_name} must be a mapping")
        nic_configs[nic_name] = nic_config
    return nic_configs


def parse_camera_configs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    camera_configs = config.get("cameras", {})
    if not isinstance(camera_configs, dict):
        raise ValueError("config key 'cameras' must be a mapping")
    return camera_configs


def get_ip_link_state(reporter: Reporter) -> dict[str, dict[str, Any]]:
    result = run_command(["ip", "-j", "link", "show"])
    if result.returncode != 0:
        reporter.error(f"failed to read interface links: {result.stderr.strip()}")
        return {}
    try:
        links = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        reporter.error(f"failed to parse 'ip -j link show' output: {exc}")
        return {}
    return {link["ifname"]: link for link in links if "ifname" in link}


def get_ip_addr_state(reporter: Reporter) -> dict[str, list[str]]:
    result = run_command(["ip", "-j", "-4", "addr", "show"])
    if result.returncode != 0:
        reporter.error(f"failed to read IPv4 interface addresses: {result.stderr.strip()}")
        return {}
    try:
        addrs = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        reporter.error(f"failed to parse 'ip -j -4 addr show' output: {exc}")
        return {}

    by_interface: dict[str, list[str]] = {}
    for entry in addrs:
        ifname = entry.get("ifname")
        addr_info = entry.get("addr_info", [])
        if not ifname or not isinstance(addr_info, list):
            continue
        by_interface[ifname] = [
            f"{addr.get('local')}/{addr.get('prefixlen')}"
            for addr in addr_info
            if addr.get("family") == "inet" and addr.get("local") and addr.get("prefixlen") is not None
        ]
    return by_interface


def parse_ethtool_output(output: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in output.splitlines():
        key, sep, value = line.strip().partition(":")
        if sep:
            parsed[key.strip().lower()] = value.strip()
    return parsed


def get_ethtool_state(interface: str) -> CommandResult:
    return run_command(["ethtool", interface])


def get_nmcli_device_status(reporter: Reporter) -> dict[str, dict[str, str]]:
    if shutil.which("nmcli") is None:
        reporter.error("nmcli is not installed, but NetworkManager is expected")
        return {}

    result = run_command(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device", "status"])
    if result.returncode != 0:
        reporter.error(f"failed to read NetworkManager device status: {result.stderr.strip()}")
        return {}

    status: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4:
            continue
        device, device_type, state, connection = parts
        status[device] = {
            "type": device_type,
            "state": state,
            "connection": connection,
        }
    return status


def get_nmcli_connection_profiles(reporter: Reporter) -> dict[str, list[str]]:
    if shutil.which("nmcli") is None:
        return {}

    result = run_command(["nmcli", "-t", "-f", "NAME,UUID,DEVICE", "connection", "show"])
    if result.returncode != 0:
        reporter.error(f"failed to read NetworkManager connection profiles: {result.stderr.strip()}")
        return {}

    profiles: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        name, _uuid, device = parts
        if device:
            profiles.setdefault(device, []).append(name)
        profiles.setdefault(name, []).append(name)
    return profiles


def used_nics_by_camera(camera_configs: dict[str, dict[str, Any]]) -> set[str]:
    return {
        str(camera_config.get("nic_port"))
        for camera_config in camera_configs.values()
        if camera_config.get("nic_port")
    }


def report_nic_issue(reporter: Reporter, nic_name: str, message: str, strict: bool, note: str = "") -> None:
    if strict:
        reporter.error(f"{nic_name}: {message}")
    else:
        suffix = f" ({note})" if note else ""
        reporter.warn(f"{nic_name}: {message}{suffix}")


def check_schema_metadata(config: dict[str, Any], reporter: Reporter) -> None:
    schema = config.get("schema")
    if not isinstance(schema, dict):
        reporter.error("schema: missing required system_config v1 metadata")
        return

    name = schema.get("name")
    version = schema.get("version")
    if name != EXPECTED_SCHEMA_NAME:
        reporter.error(f"schema.name must be {EXPECTED_SCHEMA_NAME}, found {name}")
    if version != EXPECTED_SCHEMA_VERSION:
        reporter.error(f"schema.version must be {EXPECTED_SCHEMA_VERSION}, found {version}")
    if name == EXPECTED_SCHEMA_NAME and version == EXPECTED_SCHEMA_VERSION:
        reporter.ok(f"schema is {EXPECTED_SCHEMA_NAME} v{EXPECTED_SCHEMA_VERSION}")


def validate_system_config_v1(
    nic_configs: dict[str, dict[str, Any]],
    camera_configs: dict[str, dict[str, Any]],
    reporter: Reporter,
) -> None:
    reporter.section("Schema validation")

    if not nic_configs:
        reporter.error("nics must define at least one NIC")
    if not camera_configs:
        reporter.warn("cameras is empty")

    nic_networks: dict[str, ipaddress.IPv4Interface] = {}
    for nic_name, nic_config in nic_configs.items():
        role = nic_config.get("role")
        if role not in NIC_ROLES:
            reporter.error(f"{nic_name}: role must be one of {sorted(NIC_ROLES)}, found {role}")

        managed = normalize_bool(nic_config.get("managed"))
        if managed is None:
            reporter.error(f"{nic_name}: managed must be a boolean")
            managed = True

        expected_link = normalize_bool(nic_config.get("expected_link"))
        if expected_link is None:
            reporter.error(f"{nic_name}: expected_link must be a boolean")

        mac_address = nic_config.get("mac_address")
        if not mac_address or len(normalize_mac(str(mac_address))) != 12:
            reporter.error(f"{nic_name}: mac_address is required and must be a MAC address")

        mtu = nic_config.get("mtu")
        if managed and mtu is None:
            reporter.error(f"{nic_name}: managed NICs must define mtu")
        elif mtu is not None:
            try:
                if int(mtu) < 68:
                    reporter.error(f"{nic_name}: mtu must be at least 68")
            except (TypeError, ValueError):
                reporter.error(f"{nic_name}: mtu must be an integer")

        ip_address = nic_config.get("ip_address")
        if managed and not ip_address:
            reporter.error(f"{nic_name}: managed NICs must define ip_address")
        if ip_address:
            try:
                nic_networks[nic_name] = ipaddress.ip_interface(str(ip_address))
            except ValueError as exc:
                reporter.error(f"{nic_name}: ip_address must be IPv4/CIDR: {exc}")

        link_settings = nic_config.get("link_settings")
        if managed and not isinstance(link_settings, dict):
            reporter.error(f"{nic_name}: managed NICs must define link_settings")
        elif isinstance(link_settings, dict):
            speed = link_settings.get("speed")
            if managed and speed is None:
                reporter.error(f"{nic_name}: managed NICs must define link_settings.speed")
            elif speed is not None:
                try:
                    if int(speed) <= 0:
                        reporter.error(f"{nic_name}: link_settings.speed must be positive")
                except (TypeError, ValueError):
                    reporter.error(f"{nic_name}: link_settings.speed must be an integer")

            if managed and normalize_bool(link_settings.get("autoneg")) is None:
                reporter.error(f"{nic_name}: managed NICs must define boolean link_settings.autoneg")

    for camera_mac, camera_config in camera_configs.items():
        label = f"{camera_mac} serial={camera_config.get('serial_number', 'unknown')}"
        if len(normalize_mac(camera_mac)) != 12:
            reporter.error(f"{label}: camera key must be a MAC address")

        camera_ip = camera_config.get("ip_address")
        nic_port = camera_config.get("nic_port")
        if not camera_config.get("serial_number"):
            reporter.error(f"{label}: serial_number is required")
        if not camera_ip:
            reporter.error(f"{label}: ip_address is required")
        if not nic_port:
            reporter.error(f"{label}: nic_port is required")
            continue
        if nic_port not in nic_configs:
            reporter.error(f"{label}: nic_port {nic_port} is not defined in nics")
            continue
        if not config_bool(nic_configs[nic_port].get("managed"), True):
            reporter.error(f"{label}: nic_port {nic_port} is managed=false")
            continue
        if camera_ip and nic_port in nic_networks:
            try:
                if ipaddress.ip_address(str(camera_ip)) not in nic_networks[nic_port].network:
                    reporter.error(f"{label}: {camera_ip} is outside {nic_port} subnet {nic_networks[nic_port].network}")
            except ValueError as exc:
                reporter.error(f"{label}: invalid camera IP {camera_ip}: {exc}")

    if not reporter.errors:
        reporter.ok("system_config v1 network schema checks passed")


def check_config_identity(config: dict[str, Any], config_path: str, reporter: Reporter) -> None:
    system_info = config.get("system_info", {})
    expected_host = system_info.get("hostname") if isinstance(system_info, dict) else None
    actual_host = socket.gethostname().split(".")[0]

    reporter.ok(f"using config file {config_path}")
    if expected_host:
        if expected_host == actual_host:
            reporter.ok(f"config hostname matches local host: {actual_host}")
        else:
            reporter.warn(f"config hostname is {expected_host}, local host is {actual_host}")

    renderer = system_info.get("network_renderer") if isinstance(system_info, dict) else None
    if renderer == "NetworkManager":
        reporter.ok("config expects NetworkManager")
    elif renderer:
        reporter.warn(f"config network renderer is {renderer}, expected NetworkManager")
    else:
        reporter.warn("config does not declare system_info.network_renderer")


def check_nics(
    nic_configs: dict[str, dict[str, Any]],
    camera_nics: set[str],
    link_state: dict[str, dict[str, Any]],
    addr_state: dict[str, list[str]],
    nm_status: dict[str, dict[str, str]],
    nm_profiles: dict[str, list[str]],
    reporter: Reporter,
) -> dict[str, ipaddress.IPv4Interface]:
    nic_networks: dict[str, ipaddress.IPv4Interface] = {}

    reporter.section("NIC configuration")
    for nic_name, expected in nic_configs.items():
        reporter.debug(f"checking NIC {nic_name}")
        is_camera_nic = nic_name in camera_nics
        managed = config_bool(expected.get("managed"), True)
        expected_link = config_bool(expected.get("expected_link"), managed)
        issue_note = "managed=false" if not managed else "not assigned to a configured camera"
        reporter.ok(
            f"{nic_name}: role={expected.get('role', 'unknown')} "
            f"managed={str(managed).lower()} expected_link={str(expected_link).lower()}"
        )

        link = link_state.get(nic_name)
        if not link:
            report_nic_issue(reporter, nic_name, "interface not found", managed or is_camera_nic, issue_note)
            continue

        reporter.ok(f"{nic_name}: interface exists")

        expected_mac = expected.get("mac_address")
        actual_mac = link.get("address")
        if expected_mac and normalize_mac(expected_mac) != normalize_mac(actual_mac):
            report_nic_issue(
                reporter,
                nic_name,
                f"MAC mismatch, expected {expected_mac}, found {actual_mac}",
                managed or is_camera_nic,
                issue_note,
            )
        elif expected_mac:
            reporter.ok(f"{nic_name}: MAC matches {actual_mac}")

        expected_mtu = expected.get("mtu")
        actual_mtu = link.get("mtu")
        if expected_mtu is not None:
            try:
                expected_mtu_int = int(expected_mtu)
                actual_mtu_int = int(actual_mtu)
            except (TypeError, ValueError):
                report_nic_issue(
                    reporter,
                    nic_name,
                    f"could not compare MTU, expected {expected_mtu}, found {actual_mtu}",
                    managed or is_camera_nic,
                    issue_note,
                )
            else:
                if expected_mtu_int != actual_mtu_int:
                    report_nic_issue(
                        reporter,
                        nic_name,
                        f"MTU mismatch, expected {expected_mtu}, found {actual_mtu}",
                        managed or is_camera_nic,
                        issue_note,
                    )
                else:
                    reporter.ok(f"{nic_name}: MTU matches {actual_mtu}")

        expected_ip = expected.get("ip_address")
        if expected_ip:
            try:
                nic_networks[nic_name] = ipaddress.ip_interface(str(expected_ip))
            except ValueError as exc:
                reporter.error(f"{nic_name}: invalid configured IP address {expected_ip}: {exc}")

        if not managed:
            reporter.ok(f"{nic_name}: managed=false; skipping IP, NetworkManager, link, speed, and autonegotiation checks")
            continue

        if expected_ip:
            actual_ips = addr_state.get(nic_name, [])
            if str(expected_ip) not in actual_ips:
                report_nic_issue(
                    reporter,
                    nic_name,
                    f"IP mismatch, expected {expected_ip}, found {actual_ips or 'none'}",
                    managed,
                    issue_note,
                )
            else:
                reporter.ok(f"{nic_name}: IPv4 address matches {expected_ip}")

        nm_device = nm_status.get(nic_name)
        profiles = nm_profiles.get(nic_name, [])
        if nm_device:
            reporter.ok(
                f"{nic_name}: NetworkManager state={nm_device['state']} connection={nm_device['connection'] or '--'}"
            )
        else:
            report_nic_issue(reporter, nic_name, "not listed by NetworkManager", managed, issue_note)

        if profiles:
            reporter.ok(f"{nic_name}: NetworkManager profile present ({', '.join(sorted(set(profiles)))})")
        else:
            report_nic_issue(reporter, nic_name, "no NetworkManager connection profile found", managed, issue_note)

        ethtool_result = get_ethtool_state(nic_name)
        if ethtool_result.returncode != 0:
            report_nic_issue(
                reporter,
                nic_name,
                f"ethtool failed: {ethtool_result.stderr.strip()}",
                managed,
                issue_note,
            )
            continue

        ethtool = parse_ethtool_output(ethtool_result.stdout)
        link_detected = ethtool.get("link detected", "").lower()

        if link_detected == "yes" and expected_link:
            reporter.ok(f"{nic_name}: link detected")
        elif link_detected == "yes":
            reporter.warn(f"{nic_name}: link detected but expected_link=false")
        elif expected_link:
            reporter.error(f"{nic_name}: expected_link=true but link is not detected")
        else:
            reporter.ok(f"{nic_name}: link is not detected as expected")

        if not expected_link and link_detected != "yes":
            continue

        link_settings = expected.get("link_settings", {}) or {}
        expected_speed = link_settings.get("speed")
        actual_speed_text = ethtool.get("speed", "")
        actual_speed_match = re.search(r"([0-9]+)", actual_speed_text)
        actual_speed = int(actual_speed_match.group(1)) if actual_speed_match else None
        if expected_speed and actual_speed is not None:
            if int(expected_speed) == actual_speed:
                reporter.ok(f"{nic_name}: speed matches {actual_speed}Mb/s")
            elif is_camera_nic or link_detected == "yes":
                report_nic_issue(
                    reporter,
                    nic_name,
                    f"speed mismatch, expected {expected_speed}Mb/s, found {actual_speed_text}",
                    managed,
                    issue_note,
                )
            else:
                report_nic_issue(reporter, nic_name, "speed unavailable while link is down", managed, issue_note)

        expected_autoneg = normalize_bool(link_settings.get("autoneg"))
        actual_autoneg_text = ethtool.get("auto-negotiation", "").lower()
        if expected_autoneg is not None and actual_autoneg_text:
            actual_autoneg = actual_autoneg_text in {"on", "yes"}
            if expected_autoneg == actual_autoneg:
                reporter.ok(f"{nic_name}: autonegotiation matches {actual_autoneg_text}")
            elif link_detected == "yes" and expected_speed and actual_speed == int(expected_speed):
                reporter.warn(
                    f"{nic_name}: autonegotiation differs from config ({actual_autoneg_text}), "
                    f"but link is up at expected speed"
                )
            elif is_camera_nic:
                reporter.error(
                    f"{nic_name}: autonegotiation mismatch, expected {expected_autoneg}, found {actual_autoneg_text}"
                )
            else:
                report_nic_issue(reporter, nic_name, "autonegotiation differs while link is down", managed, issue_note)

    return nic_networks


def arping_camera(
    camera_ip: str,
    interface: str,
    count: int,
    timeout: int,
) -> CommandResult:
    return run_command(["arping", "-c", str(count), "-I", interface, camera_ip], timeout=timeout)


def parse_arping_mac(output: str) -> str | None:
    matches = re.findall(r"\[([0-9a-fA-F:]{17})\]", output)
    return matches[-1] if matches else None


def get_neighbor_mac(camera_ip: str, interface: str) -> str | None:
    result = run_command(["ip", "neigh", "show", camera_ip, "dev", interface])
    if result.returncode != 0:
        return None
    match = re.search(r"\blladdr\s+([0-9a-fA-F:]{17})\b", result.stdout)
    return match.group(1) if match else None


def check_cameras(
    camera_configs: dict[str, dict[str, Any]],
    nic_configs: dict[str, dict[str, Any]],
    nic_networks: dict[str, ipaddress.IPv4Interface],
    reporter: Reporter,
    skip_arping: bool,
    arping_count: int,
    arping_timeout: int,
) -> None:
    reporter.section("Camera configuration")

    for camera_mac, camera_config in camera_configs.items():
        serial = camera_config.get("serial_number", "unknown")
        camera_ip = camera_config.get("ip_address")
        nic_port = camera_config.get("nic_port")
        label = f"{camera_mac} serial={serial}"

        if not nic_port:
            reporter.error(f"{label}: missing nic_port")
            continue
        if nic_port not in nic_configs:
            reporter.error(f"{label}: nic_port {nic_port} is not defined in nics")
            continue
        if not camera_ip:
            reporter.error(f"{label}: missing ip_address")
            continue

        try:
            camera_addr = ipaddress.ip_address(str(camera_ip))
        except ValueError as exc:
            reporter.error(f"{label}: invalid camera IP {camera_ip}: {exc}")
            continue

        nic_interface = nic_networks.get(str(nic_port))
        if nic_interface is None:
            reporter.error(f"{label}: cannot validate subnet because {nic_port} has no valid host IP")
        elif camera_addr in nic_interface.network:
            reporter.ok(f"{label}: {camera_ip} is on {nic_port} subnet {nic_interface.network}")
        else:
            reporter.error(f"{label}: {camera_ip} is not on {nic_port} subnet {nic_interface.network}")

        if skip_arping:
            reporter.ok(f"{label}: skipping camera reachability check by request")
            continue

        result = arping_camera(str(camera_ip), str(nic_port), arping_count, arping_timeout)
        if result.returncode != 0:
            details = result.stderr.strip() or result.stdout.strip()
            reporter.error(f"{label}: arping failed on {nic_port} for {camera_ip}: {details}")
            continue

        reporter.ok(f"{label}: {camera_ip} is reachable via {nic_port}")
        arping_mac = parse_arping_mac(result.stdout) or get_neighbor_mac(str(camera_ip), str(nic_port))
        if arping_mac and normalize_mac(arping_mac) != normalize_mac(camera_mac):
            reporter.error(f"{label}: arping MAC mismatch, expected {camera_mac}, found {arping_mac}")
        elif arping_mac:
            reporter.ok(f"{label}: arping MAC matches {arping_mac}")
        else:
            reporter.debug(f"{label}: arping succeeded but no MAC address was parsed")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Pancake camera NIC network configuration.")
    parser.add_argument("legacy_config", nargs="?", help="Config file path, kept for backward compatibility.")
    parser.add_argument("--config", help="Config file path. Defaults to hosts/$(hostname -s)/config.yml.")
    parser.add_argument("--verbose", action="store_true", help="Print extra diagnostic information.")
    parser.add_argument("--skip-camera-arping", action="store_true", help="Skip active camera reachability checks.")
    parser.add_argument("--arping-count", type=int, default=2, help="ARP packets per camera check.")
    parser.add_argument("--arping-timeout", type=int, default=8, help="Timeout per camera arping command in seconds.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reporter = Reporter(verbose=args.verbose)
    config_path = resolve_config_path(args.config or args.legacy_config or default_config_path())

    try:
        config = load_config(config_path)
        nic_configs = parse_nic_configs(config)
        camera_configs = parse_camera_configs(config)
    except Exception as exc:
        reporter.error(str(exc))
        return 1

    reporter.section("Config")
    check_schema_metadata(config, reporter)
    check_config_identity(config, config_path, reporter)
    validate_system_config_v1(nic_configs, camera_configs, reporter)

    link_state = get_ip_link_state(reporter)
    addr_state = get_ip_addr_state(reporter)
    nm_status = get_nmcli_device_status(reporter)
    nm_profiles = get_nmcli_connection_profiles(reporter)

    camera_nics = used_nics_by_camera(camera_configs)
    nic_networks = check_nics(nic_configs, camera_nics, link_state, addr_state, nm_status, nm_profiles, reporter)
    check_cameras(
        camera_configs,
        nic_configs,
        nic_networks,
        reporter,
        skip_arping=args.skip_camera_arping,
        arping_count=args.arping_count,
        arping_timeout=args.arping_timeout,
    )

    reporter.section("Summary")
    if reporter.errors:
        print(f"FAILED: {len(reporter.errors)} error(s), {len(reporter.warnings)} warning(s)")
        return 1
    if reporter.warnings:
        print(f"PASSED WITH WARNINGS: {len(reporter.warnings)} warning(s)")
        return 0
    print("PASSED: network configuration matches")
    return 0


if __name__ == "__main__":
    sys.exit(main())
