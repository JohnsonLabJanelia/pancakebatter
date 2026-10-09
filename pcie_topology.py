#!/usr/bin/env python3
"""Which PCIe host bridge each device hangs off, and what shares it.

  ./pcie_topology.py            live table: per host bridge, the GPUs, NICs and NVMe drives (with mounts) below it
  ./pcie_topology.py --record   print a `pcie_topology:` block for hosts/<host>/config.yml (keeps existing notes)
  ./pcie_topology.py --check    compare the live mapping with the recorded one; exit 1 on drift

Why: on pancake0 host bridge 20 carries A16 card A (the GPUs the mlnx1 cameras land on), all three
NVMe drives and both 10 GbE ports, and the first NVMe shares one x8 chipset link with the 10 GbE
chip. Traffic that crosses that bridge during a recording has cost camera frames before (the
page-cache writeback bursts fixed by vm.dirty_*_bytes, see docs/kernel_tuning.md), so anyone
planning new traffic (bulk copies to /groups, a second recorder) needs this map. Read-only.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

import hostconfig

PCI_RE = re.compile(r"0000:([0-9a-f]{2}:[0-9a-f]{2}\.[0-7])")


def run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def pci_path(sysdev: str) -> tuple[str, list[str]]:
    """(host bridge 'XX', [function, function, ...]) for a sysfs device, from its real path."""
    real = os.path.realpath(sysdev)
    bridge = re.search(r"/pci0000:([0-9a-f]{2})/", real)
    return (bridge.group(1) if bridge else "?"), PCI_RE.findall(real)


def nvme_partitions() -> dict[str, list[dict]]:
    """nvme controller name -> [{partition, uuid, mounts}] (the stable identity of what is on the drive)."""
    out: dict[str, list[dict]] = {}
    for line in run(["lsblk", "-rno", "NAME,UUID,MOUNTPOINTS"]).splitlines():
        parts = line.split(None, 2)
        if not parts or not re.match(r"nvme\d+n\d+p\d+", parts[0]):
            continue
        ctrl = re.match(r"(nvme\d+)", parts[0]).group(1)
        uuid = parts[1] if len(parts) > 1 else ""
        mounts = [m for m in (parts[2] if len(parts) > 2 else "").replace("\\x0a", " ").split() if m.startswith("/")]
        out.setdefault(ctrl, []).append({"partition": parts[0], "uuid": uuid or None, "mounts": mounts})
    return out


def read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def live_devices() -> list[dict]:
    rows = []
    for net in sorted(glob.glob("/sys/class/net/*/device")):
        bridge, path = pci_path(net)
        rows.append({"bridge": bridge, "pci": path[-1], "kind": "nic", "name": net.split("/")[4], "path": path})
    partitions = nvme_partitions()
    for nv in sorted(glob.glob("/sys/class/nvme/nvme*/device")):
        ctrl = nv.split("/")[4]
        bridge, path = pci_path(nv)
        parts = partitions.get(ctrl, [])
        # nvmeN is assigned at boot and not stable; the controller serial and the filesystem UUIDs are
        rows.append({"bridge": bridge, "pci": path[-1], "kind": "nvme", "name": ctrl, "path": path,
                     "serial": read(f"/sys/class/nvme/{ctrl}/serial"), "model": read(f"/sys/class/nvme/{ctrl}/model"),
                     "mounts": [m for pt in parts for m in pt["mounts"]], "partitions": parts})
    for line in run(["nvidia-smi", "--query-gpu=index,name,pci.bus_id", "--format=csv,noheader"]).splitlines():
        idx, name, bus = [x.strip() for x in line.split(",")]
        pci = bus.lower()[-7:]
        bridge, path = pci_path(f"/sys/bus/pci/devices/0000:{pci}")
        rows.append({"bridge": bridge, "pci": pci, "kind": "gpu", "name": f"GPU{idx} {name}", "path": path})
    return sorted(rows, key=lambda r: (r["bridge"], r["pci"]))


def group(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["bridge"], []).append(r)
    return out


def format_table(rows: list[dict]) -> str:
    lines = []
    for bridge, devs in group(rows).items():
        lines.append(f"host bridge pci0000:{bridge}")
        for d in devs:
            extra = f"  mounts {', '.join(d['mounts'])}" if d.get("mounts") else ""
            if d.get("serial"):
                extra = f"  serial {d['serial']}" + extra
            via = " > ".join(d["path"][:-1])
            lines.append(f"  {d['pci']}  {d['kind']:4s} {d['name']:<24s}{extra}" + (f"   via {via}" if via else ""))
    return "\n".join(lines)


def record_block(rows: list[dict], existing: dict | None) -> str:
    """YAML for pcie_topology: generated device lists, hand-written per-bridge notes preserved."""
    notes = {b: v.get("note") for b, v in ((existing or {}).get("host_bridges") or {}).items()}
    doc = {"pcie_topology": {"host_bridges": {}}}
    for bridge, devs in group(rows).items():
        entry: dict = {}
        if notes.get(bridge):
            entry["note"] = notes[bridge]
        devices = []
        for d in devs:
            item = {k: d[k] for k in ("pci", "kind", "name") if k in d}
            if d.get("serial"):
                item["serial"] = d["serial"]
                item["name"] = f"{d['name']} (at record time; nvmeN is not stable, the serial is)"
            if d.get("partitions"):
                item["partitions"] = [{"uuid": pt["uuid"], "mounts": pt["mounts"]} for pt in d["partitions"] if pt.get("uuid")]
            elif d.get("mounts"):
                item["mounts"] = d["mounts"]
            devices.append(item)
        entry["devices"] = devices
        doc["pcie_topology"]["host_bridges"][bridge] = entry
    return yaml.safe_dump(doc, sort_keys=False, width=120)


def compare(recorded: dict | None, rows: list[dict]) -> list[str]:
    """Differences between the recorded device->bridge mapping and the live one (names are informational)."""
    if not recorded or not recorded.get("host_bridges"):
        return ["no pcie_topology recorded (run: pcie_topology.py --record)"]
    def ident(d):   # drives by serial (slot-independent), everything else by PCI address
        return (d["kind"], d["serial"]) if d.get("serial") else (d["kind"], d["pci"])
    rec = {ident(d): (b, d["pci"]) for b, v in recorded["host_bridges"].items() for d in (v.get("devices") or [])}
    live = {ident(r): (r["bridge"], r["pci"]) for r in rows}
    problems = []
    for key in sorted(set(rec) | set(live)):
        label = f"{key[0]} {key[1]}"
        if key not in live:
            problems.append(f"{label} recorded under bridge {rec[key][0]} is gone")
        elif key not in rec:
            problems.append(f"{label} under bridge {live[key][0]} is not recorded")
        elif rec[key][0] != live[key][0]:
            problems.append(f"{label} moved: recorded bridge {rec[key][0]}, live {live[key][0]}")
        elif rec[key][1] != live[key][1]:
            problems.append(f"{label} moved within bridge {live[key][0]}: recorded {rec[key][1]}, live {live[key][1]}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", type=Path, default=hostconfig.default_config_path())
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    rows = live_devices()
    config = yaml.safe_load(args.config.read_text()) if args.config.exists() else {}
    if args.record:
        print(record_block(rows, config.get("pcie_topology")), end="")
        return 0
    if args.check:
        problems = compare(config.get("pcie_topology"), rows)
        for p in problems:
            print(f"[FAIL] {p}")
        print(f"pcie topology: {'PASS' if not problems else 'FAIL'} ({len(problems)} differences)")
        return 1 if problems else 0
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
