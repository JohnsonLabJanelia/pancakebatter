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
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

import hostconfig

SCHEMA_PATH = hostconfig.REPO_ROOT / "schemas" / "system_config.v1.schema.json"
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


def parse_ethtool_m(text: str) -> dict[str, Any]:
    """Transceiver EEPROM (`ethtool -m`) -> schema's transceiver block."""
    f = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in text.splitlines() if ":" in l)}
    wl = re.match(r"([\d.]+)", f.get("Laser wavelength", ""))
    speed = None
    if "BR, Nominal" in f:
        m = re.match(r"(\d+)", f["BR, Nominal"])
        if m:
            speed = round(int(m.group(1)) / 1000)  # MBd -> ~Gb/s
    dist = None
    for key in ("Length (SMF)", "Length (OM3 50um)", "Length (Copper)"):
        m = re.match(r"(\d+)\s*(km|m)", f.get(key, ""))
        if m and int(m.group(1)) > 0:
            dist = int(m.group(1)) * (1000 if m.group(2) == "km" else 1)
            break
    if dist is not None and "Length (SMF)" in f and f["Length (SMF)"].startswith(tuple("123456789")):
        fiber = "SMF"
    elif any(f.get(k, "").startswith(tuple("123456789")) for k in ("Length (OM2 50um)", "Length (OM3 50um)", "Length (OM4 50um)", "Length (62.5um)")):
        fiber = "MMF"
    elif f.get("Length (Copper)", "").startswith(tuple("123456789")):
        fiber = "DAC"
    else:
        fiber = None
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


def parse_lspci_mm(text: str) -> dict[str, str]:
    return {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in text.splitlines() if ":" in l)}


def parse_lsblk(text: str) -> list[dict[str, Any]]:
    """lsblk -J -b output -> storage_devices entries for physical disks only."""
    devs = []
    for d in json.loads(text)["blockdevices"]:
        if d.get("type") != "disk":
            continue
        parts = [
            {"partition": c["name"], "fs_type": c.get("fstype"), "mount_point": c.get("mountpoint")}
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

    nvcc = run(["nvcc", "--version"]) or ""
    m = re.search(r"release ([\d.]+).*?V([\d.]+)", nvcc, re.S)
    cuda_json = read_text("/usr/local/cuda/version.json")
    if cuda_json:
        info["cuda_version"] = json.loads(cuda_json).get("cuda", {}).get("version")
    elif m:
        info["cuda_version"] = m.group(2)
    else:
        note("no CUDA toolkit found (nvcc / /usr/local/cuda/version.json); cuda_version omitted")
    ff = run(["ffmpeg", "-version"])
    if ff and (m := re.match(r"ffmpeg version (\S+)", ff)):
        info["ffmpeg_version"] = m.group(1)
    cv = run(["pkg-config", "--modversion", "opencv4"])
    if cv:
        info["opencv_version"] = cv.strip()
    trt = sorted(glob.glob("/usr/local/TensorRT-*"))
    if trt:
        info["tensorrt_version"] = Path(trt[-1]).name.removeprefix("TensorRT-")

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
    out = run(["lsblk", "-J", "-b", "-o", "NAME,MODEL,SERIAL,SIZE,TYPE,MOUNTPOINT,FSTYPE,TRAN,PHY-SEC,REV"])
    if out is None:
        note("lsblk unavailable; storage_devices omitted")
        return []
    return parse_lsblk(out)


def is_physical_nic(name: str) -> bool:
    return os.path.exists(f"/sys/class/net/{name}/device") and not os.path.exists(f"/sys/class/net/{name}/wireless")


def load_privileged() -> dict[str, dict[str, Any]]:
    """Per-NIC transceiver EEPROM text / VPD hex from the installed read-only root probe.

    Not needed when already root. `sudo -n` never prompts: if the helper isn't installed
    (install_root_probe.sh) or the sudoers rule is missing, this just returns {}.
    """
    if os.geteuid() == 0 or not os.path.exists(ROOT_PROBE):
        return {}
    out = run(["sudo", "-n", ROOT_PROBE], timeout=60)
    try:
        return json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {}


def probe_nics() -> list[dict[str, Any]]:
    links = json.loads(run(["ip", "-j", "link"]) or "[]")
    addrs = {a["ifname"]: a for a in json.loads(run(["ip", "-j", "-4", "addr"]) or "[]")}
    default_ifaces = {r.get("dev") for r in json.loads(run(["ip", "-j", "route", "show", "default"]) or "[]")}
    privileged = load_privileged()
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
        speed = ethtool.get("speed") or (max(ethtool["supported_mbps"]) if ethtool["supported_mbps"] else None)
        if not ethtool.get("speed"):
            note(f"{name}: link down, link_settings.speed is the max advertised mode ({speed}); verify against the cable/transceiver")
        module = run(["ethtool", "-m", name]) or (privileged.get(name) or {}).get("ethtool_m")
        trx = parse_ethtool_m(module) if module else {k: None for k in
              ("brand", "model", "serial_number", "speed", "wavelength", "max_distance", "fiber_type")}
        up = bool(ethtool.get("link"))
        try:
            vpd = parse_vpd(Path(f"/sys/class/net/{name}/device/vpd").read_bytes())
        except OSError:
            hexdata = (privileged.get(name) or {}).get("vpd_hex")
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


def validate(config: dict[str, Any]) -> list[str]:
    try:
        import jsonschema
    except ImportError:
        return validate_with_repo_checker(config)
    schema = json.loads(SCHEMA_PATH.read_text())
    return [f"{'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message}"
            for e in sorted(jsonschema.Draft202012Validator(schema).iter_errors(config), key=lambda e: list(e.absolute_path))]


def validate_with_repo_checker(config: dict[str, Any]) -> list[str]:
    """Fallback when jsonschema is missing: reuse check_network_settings' v1 checks, quietly."""
    import check_network_settings as cns

    class QuietReporter(cns.Reporter):
        def section(self, title): pass
        def ok(self, message): pass
        def warn(self, message): self.warnings.append(message)
        def error(self, message): self.errors.append(message)

    rep = QuietReporter()
    cns.check_schema_metadata(config, rep)
    cns.validate_system_config_v1(cns.parse_nic_configs(config), cns.parse_camera_configs(config), rep)
    return rep.errors


HEADER = """\
# DRAFT captured by capture_inventory.py -- review before use.
# Auto-filled: system_info, storage_devices, gpus, nics (hardware/link facts).
# Still needs a human: nic role/managed/expected_link/ip_address, cameras, pdus,
# kernel_tuning. NIC part/serial and transceiver details are only filled when run as root;
# otherwise enter them with gui_config_editor.py.
# NICs keep their current kernel names; rename them via the udev/NIC scripts once roles are set.
"""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--output", type=Path, help="default: hosts/<hostname>/config.captured.yml")
    ap.add_argument("--stdout", action="store_true", help="print instead of writing a file")
    args = ap.parse_args(argv)

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
