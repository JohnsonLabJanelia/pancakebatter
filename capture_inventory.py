#!/usr/bin/env python3
"""Capture this machine's hardware/driver inventory as a draft system_config v1 file.

Writes hosts/<hostname>/config.captured.yml (never overwrites config.yml). Review the
draft, set NIC roles / IPs / cameras by hand, then rename it to config.yml.

Everything is read-only. Probes that need root (dmidecode, transceiver EEPROM via
ethtool -m) are best-effort; run with sudo for fuller data. Missing tools are skipped
and reported on stderr.

Usage:
    ./capture_inventory.py                 # -> hosts/<hostname>/config.captured.yml
    ./capture_inventory.py --stdout
    ./capture_inventory.py --output /tmp/draft.yml

Refresh an existing, human-edited config with newly detected hardware (a new transceiver,
a firmware update, a swapped disk) without touching roles, IPs, cameras or anything else
you set by hand. Previews by default; nothing is erased, only added or updated:
    ./capture_inventory.py --refresh                    # preview changes to hosts/<hostname>/config.yml
    ./capture_inventory.py --refresh --write            # apply (validated, timestamped backup)
    ./capture_inventory.py --refresh --config PATH
"""
from __future__ import annotations

import argparse
import functools
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

import hostconfig
import refresh_config
from configio import load, save, validate  # noqa: F401  (validate is re-exported for other tools)

UNASSIGNED_IP = "0.0.0.0/0"
ROOT_PROBE = "/usr/local/libexec/pancakebatter/root_probe.py"  # see install_root_probe.sh
NOTES: list[str] = []


def note(msg: str) -> None:
    NOTES.append(msg)


def run(args: list[str], timeout: int = 15) -> str | None:
    """Return stdout, or None if the tool is missing, fails, or times out."""
    try:
        res = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def read_text(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


# --------------------------------------------------------------------- parsers

def parse_os_release(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


def parse_nvidia_smi_csv(text: str) -> list[dict[str, Any]]:
    gpus = []
    for line in text.strip().splitlines():
        name, driver, uuid, bus, mem, cap = [f.strip() for f in line.split(",")]
        gpus.append({
            "model": name,
            "driver_version": driver,
            "uuid": uuid,
            "pcie_id": bus.split(":", 1)[1].lower() if bus.count(":") >= 2 else bus.lower(),
            "memory_mib": int(mem.split()[0]),
            "cuda_capability": "sm_" + cap.replace(".", ""),
        })
    return gpus


def parse_ethtool(text: str) -> dict[str, Any]:
    """Pull speed/autoneg/link/supported speeds out of plain `ethtool <if>`."""
    info: dict[str, Any] = {"supported_mbps": []}
    in_supported = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Supported link modes:"):
            in_supported = True
            info["supported_mbps"] += [int(m) for m in re.findall(r"(\d+)base", stripped)]
        elif in_supported and line.startswith("\t ") :  # continuation of the mode list
            info["supported_mbps"] += [int(m) for m in re.findall(r"(\d+)base", stripped)]
        else:
            in_supported = False
            if stripped.startswith("Speed:"):
                m = re.match(r"Speed:\s*(\d+)Mb/s", stripped)
                info["speed"] = int(m.group(1)) if m else None
            elif stripped.startswith("Auto-negotiation:"):
                info["autoneg"] = stripped.split(":", 1)[1].strip() == "on"
            elif stripped.startswith("Link detected:"):
                info["link"] = stripped.split(":", 1)[1].strip().startswith("yes")
    return info


def parse_ethtool_i(text: str) -> dict[str, str]:
    return {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in text.splitlines() if ":" in l)}


STANDARD_RATES_GBPS = (1, 10, 25, 40, 50, 100, 200, 400)


def _length_m(fields: dict[str, str], *keys: str) -> int:
    """Largest positive length (metres) among the given `Length (...)` fields; km fields are scaled."""
    best = 0
    for key in keys:
        m = re.match(r"(\d+)\s*(km|m)\b", fields.get(key, ""))
        if m:
            best = max(best, int(m.group(1)) * (1000 if m.group(2) == "km" else 1))
    return best


def parse_ethtool_m(text: str) -> dict[str, Any]:
    """Transceiver EEPROM (`ethtool -m`) -> schema's transceiver block."""
    f = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in text.splitlines() if ":" in l)}
    wl = re.match(r"([\d.]+)", f.get("Laser wavelength", ""))
    speed = None
    m = re.match(r"(\d+)\s*MBd", f.get("BR, Nominal", ""))
    if m:  # signalling rate -> nearest standard Ethernet rate (25750 MBd -> 25 Gb/s)
        speed = min(STANDARD_RATES_GBPS, key=lambda r: abs(r - int(m.group(1)) / 1000))
    smf = _length_m(f, "Length (SMF,km)", "Length (SMF)")
    mmf = _length_m(f, "Length (OM2 50um)", "Length (OM3 50um)", "Length (OM4 50um)", "Length (50um)",
                    "Length (62.5um)", "Length (OM1)", "Length (OM2)", "Length (OM3)", "Length (OM4)")
    copper = _length_m(f, "Length (Copper)")
    fiber, dist = (("SMF", smf) if smf else ("MMF", mmf) if mmf else ("DAC", copper) if copper else (None, None))
    return {
        "brand": f.get("Vendor name") or None,
        "model": f.get("Vendor PN") or None,
        "serial_number": f.get("Vendor SN") or None,
        "speed": speed,
        "wavelength": int(float(wl.group(1))) if wl else None,
        "max_distance": dist,
        "fiber_type": fiber,
    }


def parse_vpd(data: bytes) -> dict[str, str]:
    """PCI Vital Product Data -> {'PN': ..., 'SN': ..., ...} from the read-only (0x90) section."""
    out: dict[str, str] = {}
    i = 0
    while i + 3 <= len(data):
        tag = data[i]
        if tag == 0x78:  # end tag
            break
        if tag & 0x80:  # large resource: tag, 2-byte little-endian length
            length = int.from_bytes(data[i + 1:i + 3], "little")
            body = data[i + 3:i + 3 + length]
            if tag == 0x90:
                j = 0
                while j + 3 <= len(body):
                    key, n = body[j:j + 2].decode("ascii", "replace"), body[j + 2]
                    out[key] = body[j + 3:j + 3 + n].decode("ascii", "replace").strip()
                    j += 3 + n
            i += 3 + length
        else:  # small resource, nothing we need
            i += 1 + (tag & 0x07)
    return out


def parse_dmi_slots(text: str) -> list[dict[str, str]]:
    """`dmidecode -t slot` -> [{designation, type, usage, bus}], bus as 'bb:dd' (hex, lowercase)."""
    slots = []
    for block in re.split(r"\n\s*\n", text):
        if "System Slot Information" not in block:
            continue
        f = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in block.splitlines() if ":" in l)}
        m = re.match(r"[0-9a-f]{4}:([0-9a-f]{2}:[0-9a-f]{2})\.\d", f.get("Bus Address", ""), re.I)
        slots.append({"designation": f.get("Designation"), "type": f.get("Type"),
                      "usage": f.get("Current Usage"), "bus": m.group(1).lower() if m else None})
    return slots


def slot_for_device(slots: list[dict[str, str]], pcie_id: str | None) -> str | None:
    """Slot designation for a device, matching it or any bridge above it (firmware varies)."""
    if not pcie_id:
        return None
    by_bus = {s["bus"]: s["designation"] for s in slots if s.get("bus")}
    chain = [pcie_id]
    try:
        real = os.path.realpath(f"/sys/bus/pci/devices/0000:{pcie_id}")
        chain += [p[5:] for p in reversed(real.split("/")) if re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d", p)]
    except OSError:
        pass
    for addr in chain:
        if addr[:5].lower() in by_bus:
            return by_bus[addr[:5].lower()]
    return None


def parse_lspci_mm(text: str) -> dict[str, str]:
    return {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in text.splitlines() if ":" in l)}


def parse_lsblk(text: str) -> list[dict[str, Any]]:
    """lsblk -J -b output -> storage_devices entries for physical disks only."""
    devs = []
    for d in json.loads(text)["blockdevices"]:
        if d.get("type") != "disk":
            continue
        parts = [
            {"partition": c["name"], "fs_type": c.get("fstype"), "mount_point": c.get("mountpoint"), "uuid": c.get("uuid")}
            for c in d.get("children") or [] if c.get("type") == "part"
        ]
        devs.append({
            "name": d["name"],
            "model": (d.get("model") or "").strip() or None,
            "serial_number": d.get("serial"),
            "capacity": round(d["size"] / 1e12, 2),  # TB
            "device_path": f"/dev/{d['name']}",
            "firmware_version": d.get("rev"),
            "format": d.get("phy-sec"),
            "transport": d.get("tran"),
            "partitions": parts,
        })
    return devs


# ---------------------------------------------------------------------- probes

ESDK_LIB_GLOB = "/opt/EVT/eSDK/lib/libEmergentCamera.so.*"
ORANGE_FFMPEG = "/opt/orange/lib/ffmpeg-nvidia/bin/ffmpeg"
ORANGE_OPENCV = "/opt/orange/lib/opencv"


def esdk_version_from_libs(paths) -> str | None:
    """'/opt/EVT/eSDK/lib/libEmergentCamera.so.2.55.02' -> '2.55.02' (highest if several).

    The dpkg package (emergent-esdk-ecapture) is versioned 1.0 regardless of the SDK inside it, and
    EVT_SDKVersion() needs the library loaded with its dependencies; the soname is the reliable source.
    """
    versions = []
    for path in paths:
        name = Path(path).name
        if ".so." in name:
            v = name.split(".so.", 1)[1]
            if re.fullmatch(r"\d+(\.\d+)+", v):
                versions.append(v)
    return max(versions, key=lambda v: tuple(int(x) for x in v.split("."))) if versions else None


def normalize_ffmpeg_version(raw: str) -> str:
    """'n4.4.5-7-g283dc2e8eb' (git describe of a source build) -> '4.4.5'; plain versions pass through."""
    m = re.match(r"n?(\d+(?:\.\d+)+)", raw)
    return m.group(1) if m else raw


def probe_system_info() -> dict[str, Any]:
    osr = parse_os_release(read_text("/etc/os-release") or "")
    info: dict[str, Any] = {
        "hostname": socket.gethostname().split(".")[0],
        "ubuntu_version": osr.get("VERSION_ID"),
        "kernel_version": os.uname().release,
    }
    cpu = run(["lscpu", "-J"])
    if cpu:
        fields = {e["field"].rstrip(":"): e["data"] for e in json.loads(cpu)["lscpu"]}
        info["cpu_model"] = fields.get("Model name")
        info["cpu_threads"] = int(fields["CPU(s)"]) if "CPU(s)" in fields else None
    mem = read_text("/proc/meminfo")
    if mem:
        info["memory_gb"] = round(int(re.search(r"MemTotal:\s+(\d+)", mem).group(1)) / 1024 / 1024)
    for key, f in (("board_vendor", "board_vendor"), ("board_name", "board_name"), ("bios_version", "bios_version")):
        info[key] = read_text(f"/sys/class/dmi/id/{f}")
    cmdline = read_text("/proc/cmdline")
    if cmdline:
        info["kernel_cmdline"] = cmdline

    nvcc_bin = "nvcc" if shutil.which("nvcc") else "/usr/local/cuda/bin/nvcc"
    nvcc = run([nvcc_bin, "--version"]) or ""
    m = re.search(r"release ([\d.]+).*?V([\d.]+)", nvcc, re.S)
    cuda_json = read_text("/usr/local/cuda/version.json")
    if m:
        info["cuda_version"] = m.group(2)          # nvcc's release build, e.g. 12.2.140
    elif cuda_json:
        info["cuda_version"] = json.loads(cuda_json).get("cuda", {}).get("version")   # a build stamp like 12.2.20230823
    else:
        note("no CUDA toolkit found (nvcc / /usr/local/cuda/version.json); cuda_version omitted")
    # The rig's own ffmpeg/OpenCV builds live under /opt/orange; fall back to whatever is on PATH.
    ff = run([ORANGE_FFMPEG if Path(ORANGE_FFMPEG).exists() else "ffmpeg", "-version"])
    if ff and (m := re.match(r"ffmpeg version (\S+)", ff)):
        info["ffmpeg_version"] = normalize_ffmpeg_version(m.group(1))
    cv = None
    if Path(f"{ORANGE_OPENCV}/bin/opencv_version").exists():
        cv = run([f"{ORANGE_OPENCV}/bin/opencv_version"])
    if not cv:
        cv = run(["pkg-config", "--modversion", "opencv4"])
    if cv:
        info["opencv_version"] = cv.strip()
    trt = sorted(glob.glob("/usr/local/TensorRT-*"))
    if trt:
        info["tensorrt_version"] = Path(trt[-1]).name.removeprefix("TensorRT-")
    drv = read_text("/sys/module/nvidia/version")
    if drv:
        info["nvidia_driver_version"] = drv.strip()
    else:
        note("nvidia kernel module not loaded; nvidia_driver_version omitted")
    esdk = esdk_version_from_libs(glob.glob(ESDK_LIB_GLOB))
    if esdk:
        info["esdk_version"] = esdk
    else:
        note("no Emergent eSDK library under /opt/EVT/eSDK/lib; esdk_version omitted")

    nm_active = (run(["systemctl", "is-active", "NetworkManager"]) or "").strip() == "active"
    info["network_renderer"] = "NetworkManager" if nm_active else "unknown"
    if not nm_active:
        note("NetworkManager is not active; schema requires network_renderer: NetworkManager")
    nm_ver = run(["nmcli", "-v"])
    if nm_ver and (m := re.search(r"([\d.]+)\s*$", nm_ver.strip())):
        info["network_manager_version"] = m.group(1)
    return info


def probe_gpus() -> list[dict[str, Any]]:
    out = run(["nvidia-smi", "--query-gpu=name,driver_version,uuid,pci.bus_id,memory.total,compute_cap",
               "--format=csv,noheader"])
    if out is None:
        note("nvidia-smi unavailable; gpus omitted")
        return []
    return parse_nvidia_smi_csv(out)


def probe_storage() -> list[dict[str, Any]]:
    out = run(["lsblk", "-J", "-b", "-o", "NAME,MODEL,SERIAL,SIZE,TYPE,MOUNTPOINT,FSTYPE,TRAN,PHY-SEC,REV,UUID"])
    if out is None:
        note("lsblk unavailable; storage_devices omitted")
        return []
    return parse_lsblk(out)


def is_physical_nic(name: str) -> bool:
    return os.path.exists(f"/sys/class/net/{name}/device") and not os.path.exists(f"/sys/class/net/{name}/wireless")


@functools.cache
def load_privileged() -> dict[str, Any]:
    """Per-NIC transceiver EEPROM text / VPD hex from the installed read-only root probe.

    Not needed when already root. `sudo -n` never prompts: if the helper isn't installed
    (install_root_probe.sh) or the sudoers rule is missing, this just returns {}.
    """
    if os.geteuid() == 0 or not os.path.exists(ROOT_PROBE):
        return {}
    out = run(["sudo", "-n", ROOT_PROBE], timeout=60)
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {}
    return data if "nics" in data else {"nics": data, "dmi_slots": None}  # tolerate older helper output


def load_slots(privileged: dict[str, Any]) -> list[dict[str, str]]:
    text = run(["dmidecode", "-t", "slot"]) or privileged.get("dmi_slots")
    return parse_dmi_slots(text) if text else []


def probe_nics() -> list[dict[str, Any]]:
    links = json.loads(run(["ip", "-j", "link"]) or "[]")
    addrs = {a["ifname"]: a for a in json.loads(run(["ip", "-j", "-4", "addr"]) or "[]")}
    default_ifaces = {r.get("dev") for r in json.loads(run(["ip", "-j", "route", "show", "default"]) or "[]")}
    privileged = load_privileged()
    slots = load_slots(privileged)
    nics = []
    for link in sorted(links, key=lambda l: l["ifname"]):
        name = link["ifname"]
        if not is_physical_nic(name):
            continue
        ethtool = parse_ethtool(run(["ethtool", name]) or "")
        drv = parse_ethtool_i(run(["ethtool", "-i", name]) or "")
        bus = drv.get("bus-info", "")
        pcie_id = bus.split(":", 1)[1] if bus.count(":") >= 2 else None
        pci = parse_lspci_mm(run(["lspci", "-s", pcie_id, "-mm", "-v"]) or "") if pcie_id else {}

        ipv4 = next((f"{a['local']}/{a['prefixlen']}" for a in addrs.get(name, {}).get("addr_info", [])), None)
        module = run(["ethtool", "-m", name]) or (privileged.get("nics", {}).get(name) or {}).get("ethtool_m")
        trx = parse_ethtool_m(module) if module else {k: None for k in
              ("brand", "model", "serial_number", "speed", "wavelength", "max_distance", "fiber_type")}
        speed = ethtool.get("speed")
        if not speed:  # link down: prefer the plugged module's rating over the port's max advertised mode
            max_advertised = max(ethtool["supported_mbps"]) if ethtool["supported_mbps"] else None
            speed = trx["speed"] * 1000 if trx["speed"] else max_advertised
            source = "transceiver rating" if trx["speed"] else "max advertised mode"
            note(f"{name}: link down, link_settings.speed is the {source} ({speed}); verify against the cable/transceiver")
        up = bool(ethtool.get("link"))
        try:
            vpd = parse_vpd(Path(f"/sys/class/net/{name}/device/vpd").read_bytes())
        except OSError:
            hexdata = (privileged.get("nics", {}).get(name) or {}).get("vpd_hex")
            vpd = parse_vpd(bytes.fromhex(hexdata)) if hexdata else {}
        nics.append({name: {
            "altname": (link.get("altnames") or [None])[0],
            "role": "management" if name in default_ifaces else "unknown",
            "managed": False,
            "expected_link": up,
            "mac_address": link.get("address"),
            "brand": pci.get("Vendor"),
            "model": pci.get("Device"),
            "part_number": vpd.get("PN"),
            "pcie_id": pcie_id,
            "pcie_slot": slot_for_device(slots, pcie_id),
            "subsystem": pci.get("SDevice"),
            "serial_number": vpd.get("SN"),
            "driver": drv.get("driver"),
            "firmware_version": drv.get("firmware-version"),
            "mtu": link.get("mtu"),
            "ip_address": ipv4 or UNASSIGNED_IP,
            "link_settings": {"speed": speed, "autoneg": ethtool.get("autoneg", True)},
            "transceiver": trx,
        }})
    missing = any(v["transceiver"]["brand"] is None or v["serial_number"] is None for n in nics for v in n.values())
    if missing and os.geteuid() != 0 and not privileged:
        note("transceiver details (ethtool -m) and NIC part/serial numbers (PCI VPD) need root: "
             "run `sudo ./install_root_probe.sh` once (read-only helper), re-run with sudo, or use gui_config_editor.py")
    return nics


# ------------------------------------------------------------------- assembling

def build_config() -> dict[str, Any]:
    gpus = probe_gpus()
    info = probe_system_info()
    slots = load_slots(load_privileged())
    if slots:
        info["pcie_slots"] = slots
    else:
        note("PCIe slot table unavailable (dmidecode needs root); run install_root_probe.sh or sudo to record slots")
    caps = sorted({g["cuda_capability"] for g in gpus})
    if gpus:
        info["nvidia_driver_version"] = sorted({g["driver_version"] for g in gpus})[0]
        info["cuda_capability"] = caps[0] if len(caps) == 1 else caps
    return {
        "schema": {"name": "system_config", "version": 1},
        "system_info": info,
        "storage_devices": probe_storage(),
        "gpus": gpus,
        "nics": probe_nics(),
        "cameras": {},
    }


HEADER = """\
# DRAFT captured by capture_inventory.py -- review before use.
# Auto-filled: system_info, storage_devices, gpus, nics (hardware/link facts).
# Still needs a human: nic role/managed/expected_link/ip_address, cameras, pdus,
# kernel_tuning. NIC part/serial and transceiver details are only filled when run as root;
# otherwise enter them with gui_config_editor.py.
# NICs keep their current kernel names; rename them via the udev/NIC scripts once roles are set.
"""


REFRESH_SECTIONS = ("system_info", "nics", "storage_devices", "gpus")


def refresh(config_path: Path, write: bool, only: list[str] | None = None) -> int:
    """Preview (or, with write=True, apply) newly captured facts on top of an existing config.

    `only` limits the refresh to those top-level sections (e.g. ["system_info"] after a software
    upgrade); the others are passed through unchanged so they produce no changes.
    """
    if not config_path.exists():
        raise SystemExit(f"{config_path} does not exist; capture a draft first (no --refresh)")
    fresh = build_config()
    if only:
        bad = sorted(set(only) - set(REFRESH_SECTIONS))
        if bad:
            raise SystemExit(f"--only accepts {', '.join(REFRESH_SECTIONS)}; got {bad}")
        for section in REFRESH_SECTIONS:
            if section not in only:
                fresh.pop(section, None)   # absent = "no new facts" for the merge, so no changes are reported
    try:
        merged, changes, notes = refresh_config.merge_facts(load(config_path), fresh)
    except refresh_config.RefreshError as exc:
        raise SystemExit(str(exc))

    print(f"Refreshing {config_path}")
    if changes:
        print(f"\n{len(changes)} change(s):")
        for c in changes:
            print(f"  ~ {c}")
    else:
        print("\nNo changes: recorded hardware facts already match this machine.")
    capture_notes = [n for n in NOTES if "link down" not in n]
    if notes or capture_notes:
        print("\nNotes:")
        for n in notes + capture_notes:
            print(f"  - {n}")

    problems = validate(merged)
    if problems:
        print("\nThe merged config would not validate; nothing written:", file=sys.stderr)
        for p in problems:
            print(f"  schema: {p}", file=sys.stderr)
        return 1
    if not changes:
        return 0
    if not write:
        print("\nPreview only. Re-run with --write to apply (a timestamped backup is kept).")
        return 0
    errors = save(config_path, merged)
    if errors:
        for e in errors:
            print(f"  schema: {e}", file=sys.stderr)
        return 1
    print(f"\nWrote {config_path} (previous version saved as {config_path.name}.bak.<timestamp>).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, help="default: hosts/<hostname>/config.captured.yml")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing a file")
    ap.add_argument("--refresh", action="store_true",
                    help="merge newly detected hardware facts into an existing config (preview unless --write)")
    ap.add_argument("--write", action="store_true", help="with --refresh: apply the changes")
    ap.add_argument("--only", action="append", metavar="SECTION",
                    help=f"with --refresh: limit to a section ({', '.join(REFRESH_SECTIONS)}); repeatable")
    ap.add_argument("--config", type=Path, help="with --refresh: default hosts/<hostname>/config.yml")
    args = ap.parse_args(argv)
    if args.write and not args.refresh:
        ap.error("--write only applies to --refresh")
    if args.refresh:
        return refresh(args.config or hostconfig.default_config_path(socket.gethostname().split(".")[0]), args.write, args.only)

    config = build_config()
    text = HEADER + yaml.safe_dump(config, sort_keys=False, width=120)
    if args.stdout:
        sys.stdout.write(text)
    else:
        out = args.output or hostconfig.host_file("config.captured.yml", config["system_info"]["hostname"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text)
        print(f"wrote {out}", file=sys.stderr)

    problems = validate(config)
    for n in NOTES:
        print(f"note: {n}", file=sys.stderr)
    for p in problems:
        print(f"schema: {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
