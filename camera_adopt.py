#!/usr/bin/env python3
"""Adopt cameras that are plugged into camera NIC ports: discover, assign IP, record in config.

For each camera the eSDK can see (including ones whose IP is outside the port's subnet):
  1. match it to a host port by the NIC that saw it (camera_discover, a read-only eSDK probe);
  2. plan the port's camera IP (host IP + 1, e.g. port 192.168.110.1/24 -> camera 192.168.110.2);
  3. program that IP persistently with the vendor's own `evttools -f <port_ip> -o bp`;
  4. rediscover on the port to prove the camera answers at the new IP;
  5. only then add/update its entry under `cameras:` in hosts/<hostname>/config.yml.

Previews by default. `--apply` changes the camera and the config, and needs root for the
camera programming (use: sudo "$(command -v python3)" ./camera_adopt.py --apply).

Refuses (changes nothing) when: a camera is on a port that is not a managed camera NIC, two
cameras share a port (`evttools -f` would force both), a camera is already configured on a
different port (use camera_net_config.py move), or the target IP conflicts with another camera.
Lens details are not reported by cameras: add them in gui_config_editor.py afterwards.

    ./camera_adopt.py                      # preview
    sudo "$(command -v python3)" ./camera_adopt.py --apply [--reboot-check]
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import camera_net_config as cnc
import check_network_settings as cns
import configio
import hostconfig

REPO_ROOT = hostconfig.REPO_ROOT
ESDK_DIR = Path(os.environ.get("EVT_ESDK_DIR", "/opt/EVT/eSDK"))
DISCOVER_SRC = REPO_ROOT / "camera_discover.cpp"
DISCOVER_BIN = REPO_ROOT / "build" / "camera_discover"
EVTTOOLS = ESDK_DIR / "tools" / "evttools"


class AdoptError(Exception):
    pass


@dataclass
class Plan:
    device: dict[str, Any]
    port: str
    port_ip: ipaddress.IPv4Interface
    target_ip: ipaddress.IPv4Address
    config_key: str            # key under cameras:
    entry: dict[str, Any]      # entry to write
    config_action: str         # add | update | none
    needs_program: bool


# ------------------------------------------------------------------ discovery

def ensure_discover_binary() -> Path:
    if DISCOVER_BIN.exists() and DISCOVER_BIN.stat().st_mtime >= DISCOVER_SRC.stat().st_mtime:
        return DISCOVER_BIN
    gxx = shutil.which("g++")
    if not gxx:
        raise AdoptError("g++ not found; install build-essential to build camera_discover")
    DISCOVER_BIN.parent.mkdir(exist_ok=True)
    cmd = [gxx, "-O2", f"-I{ESDK_DIR}/include", str(DISCOVER_SRC), "-o", str(DISCOVER_BIN),
           f"-L{ESDK_DIR}/lib", "-lEmergentCamera", "-lEmergentGenICam", "-lEmergentGigEVision",
           f"-Wl,-rpath,{ESDK_DIR}/lib"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise AdoptError(f"building camera_discover failed:\n{res.stderr}")
    return DISCOVER_BIN


def discover(extra: list[str] | None = None) -> list[dict[str, Any]]:
    """Run the read-only eSDK discovery probe; returns the device list."""
    binary = ensure_discover_binary()
    res = subprocess.run([str(binary), *(extra or [])], capture_output=True, text=True, timeout=30)
    if res.returncode != 0:
        raise AdoptError(f"camera_discover failed: {res.stderr.strip()}")
    return json.loads(res.stdout)["devices"]


# ------------------------------------------------------------------- planning

def config_mac(mac: str) -> str:
    return mac.upper()  # discovery gives e0-55-97-..; config keys are E0-55-97-..


def serial_value(serial: str) -> int | str:
    return int(serial) if serial.isdigit() else serial


def plan_adoption(devices: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[Plan], list[str]]:
    nics = cns.parse_nic_configs(config)
    cameras = cns.parse_camera_configs(config) or {}
    plans: list[Plan] = []
    problems: list[str] = []

    by_port: dict[str, list[dict[str, Any]]] = {}
    for d in devices:
        by_port.setdefault(d["nic"]["name"], []).append(d)

    for port, devs in by_port.items():
        if len(devs) > 1:
            serials = ", ".join(d["serial"] for d in devs)
            problems.append(f"{port}: {len(devs)} cameras on one port ({serials}); evttools would force all of them. "
                            "Connect one at a time.")
            continue
        d = devs[0]
        label = f"camera {d['serial']} ({d['model']}) on {port}"
        nic = nics.get(port)
        if nic is None:
            problems.append(f"{label}: {port} is not in the config's nics")
            continue
        if not cns.config_bool(nic.get("managed"), True) or nic.get("role") != "camera":
            problems.append(f"{label}: {port} is not an active camera port "
                            f"(run: camera_net_config.py activate-nic --nic {port} --apply)")
            continue
        try:
            port_ip = cnc.get_nic_interface(port, nic)
            inferred = cnc.infer_camera_ip(port_ip)
        except cnc.CameraNetConfigError as exc:
            problems.append(f"{label}: {exc}")
            continue

        mac = config_mac(d["mac"])
        existing_key = next((k for k, v in cameras.items()
                             if cns.normalize_mac(k) == cns.normalize_mac(mac)
                             or str(v.get("serial_number")) == d["serial"]), None)
        if existing_key is not None:
            old = cameras[existing_key]
            if old.get("nic_port") != port:
                problems.append(f"{label}: configured on {old.get('nic_port')}, found on {port}; "
                                f"use camera_net_config.py move --camera {d['serial']} --nic {port}")
                continue
            if str(old.get("ip_address")) != str(inferred):
                problems.append(f"{label}: config says {old.get('ip_address')}, but evttools can only assign {inferred} "
                                "on this port")
                continue
            entry = dict(old)
            entry.setdefault("model", d["model"])
            action = "update" if entry != old else "none"
            key = existing_key
        else:
            try:
                cnc.check_ip_conflicts(cameras, mac, inferred, port)
            except cnc.CameraNetConfigError as exc:
                problems.append(f"{label}: {exc}")
                continue
            entry = {"model": d["model"], "serial_number": serial_value(d["serial"]),
                     "ip_address": str(inferred), "nic_port": port}
            action, key = "add", mac

        needs_program = d["ip"] != str(inferred) or not d.get("persistent_ip_active")
        plans.append(Plan(d, port, port_ip, inferred, key, entry, action, needs_program))
    return plans, problems


def print_plan(plans: list[Plan], problems: list[str], config_path: Path) -> None:
    print(f"Config: {config_path}")
    for p in plans:
        d = p.device
        print(f"\nCamera {d['serial']} ({d['model']}, MAC {d['mac']}) on {p.port} (host {p.port_ip.ip})")
        print(f"  current IP: {d['ip']}/{d['mask']}  persistent: {d.get('persistent_ip_active')}")
        if p.needs_program:
            print(f"  program:    {p.target_ip}  via  {EVTTOOLS} -f {p.port_ip.ip} -o bp")
        else:
            print(f"  program:    nothing to do (already {p.target_ip}, persistent)")
        print(f"  config:     {p.config_action} cameras[{p.config_key}] = {p.entry}")
    for msg in problems:
        print(f"\nPROBLEM: {msg}")
    if not plans and not problems:
        print("\nNo cameras discovered. Check fiber/link (carrier) and camera power.")


# --------------------------------------------------------------------- apply

def verify_on_port(plan: Plan) -> None:
    devs = discover(["--if", plan.port, "--timeout-ms", "2000"])
    match = [d for d in devs if d["serial"] == plan.device["serial"]]
    if not match:
        raise AdoptError(f"camera {plan.device['serial']} not found on {plan.port} after programming")
    if match[0]["ip"] != str(plan.target_ip):
        raise AdoptError(f"camera {plan.device['serial']} answers at {match[0]['ip']}, expected {plan.target_ip}")
    if not match[0].get("persistent_ip_active"):
        raise AdoptError(f"camera {plan.device['serial']} is at {plan.target_ip} but not persistent")


def program_camera(plan: Plan, args: argparse.Namespace) -> None:
    cmd = [str(args.evttools), "-f", str(plan.port_ip.ip), "-o", "bp"]
    print(f"\nProgramming {plan.device['serial']}: {' '.join(cmd)}")
    res = cnc.run_command(cmd, timeout=args.evttools_timeout)
    if res.stdout:
        print(res.stdout, end="")
    if res.returncode != 0:
        raise AdoptError(f"evttools failed (exit {res.returncode}); config not updated.\n{res.stderr}")
    time.sleep(args.settle_seconds)


def reboot_check(plan: Plan, args: argparse.Namespace) -> None:
    print(f"\nRebooting {plan.device['serial']} to prove the IP persists...")
    res = cnc.run_command([str(args.evttools), "-r", plan.device["serial"]], timeout=args.evttools_timeout)
    if res.returncode != 0:
        raise AdoptError(f"evttools reboot failed (exit {res.returncode})")
    time.sleep(args.reboot_wait)
    deadline = time.time() + 60
    while True:
        try:
            verify_on_port(plan)
            print("  OK: camera came back at the persistent IP")
            return
        except AdoptError as exc:
            if time.time() > deadline:
                raise AdoptError(f"after reboot: {exc}")
            time.sleep(3)


def apply_plans(plans: list[Plan], config_path: Path, config: dict[str, Any], args: argparse.Namespace) -> None:
    if any(p.needs_program for p in plans) and os.geteuid() != 0:
        raise AdoptError('programming cameras needs root: sudo "$(command -v python3)" ./camera_adopt.py --apply')
    for p in plans:
        if p.needs_program:
            program_camera(p, args)
        verify_on_port(p)
        print(f"  OK: {p.device['serial']} answers at {p.target_ip} on {p.port}")
        if args.reboot_check:
            reboot_check(p, args)

    changed = [p for p in plans if p.config_action != "none"]
    if not changed:
        print("\nConfig already up to date.")
        return
    cameras = config.get("cameras")
    if not isinstance(cameras, dict):
        cameras = config["cameras"] = {}
    for p in changed:
        cameras[p.config_key] = p.entry
    errors = configio.save(config_path, config)
    if errors:
        raise AdoptError("config would not validate; not written:\n  " + "\n  ".join(errors))
    print(f"\nWrote {config_path} (timestamped .bak kept). Add lens details in gui_config_editor.py, then run "
          "check_network_settings.py.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="program cameras and update the config (default: preview)")
    ap.add_argument("--config", type=Path, default=hostconfig.default_config_path())
    ap.add_argument("--evttools", type=Path, default=EVTTOOLS)
    ap.add_argument("--evttools-timeout", type=int, default=90)
    ap.add_argument("--settle-seconds", type=float, default=5.0)
    ap.add_argument("--reboot-check", action="store_true",
                    help="with --apply: reboot each camera and confirm the IP persisted")
    ap.add_argument("--reboot-wait", type=float, default=30.0)
    args = ap.parse_args(argv)

    try:
        config = configio.load(args.config)
        cnc.require_system_config_v1(config)
        devices = discover(["--broadcast", "--timeout-ms", "2500"])
        plans, problems = plan_adoption(devices, config)
        print_plan(plans, problems, args.config)
        if problems:
            print("\nNothing changed: fix the problems above first.", file=sys.stderr)
            return 1
        if not plans:
            return 0
        if not args.apply:
            print("\nPreview only. Re-run with --apply to program the camera(s) and update the config.")
            return 0
        apply_plans(plans, args.config, config, args)
    except (AdoptError, cnc.CameraNetConfigError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
