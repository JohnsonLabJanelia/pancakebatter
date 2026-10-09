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


def nvme_mounts() -> dict[str, list[str]]:
    """nvme controller name -> mount points of its partitions."""
    out: dict[str, list[str]] = {}
    for line in run(["lsblk", "-rno", "NAME,MOUNTPOINTS"]).splitlines():
        parts = line.split(None, 1)
        if not parts or not parts[0].startswith("nvme"):
            continue
        ctrl = re.match(r"(nvme\d+)", parts[0]).group(1)
        if len(parts) > 1 and parts[1].strip():
            out.setdefault(ctrl, []).extend(m for m in parts[1].replace("\\x0a", " ").split() if m.startswith("/"))
    return out


def live_devices() -> list[dict]:
    rows = []
    for net in sorted(glob.glob("/sys/class/net/*/device")):
        bridge, path = pci_path(net)
        rows.append({"bridge": bridge, "pci": path[-1], "kind": "nic", "name": net.split("/")[4], "path": path})
    mounts = nvme_mounts()
    for nv in sorted(glob.glob("/sys/class/nvme/nvme*/device")):
        ctrl = nv.split("/")[4]
        bridge, path = pci_path(nv)
        rows.append({"bridge": bridge, "pci": path[-1], "kind": "nvme", "name": ctrl, "path": path,
                     "mounts": mounts.get(ctrl, [])})
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
        entry["devices"] = [{k: d[k] for k in ("pci", "kind", "name") if k in d} | ({"mounts": d["mounts"]} if d.get("mounts") else {})
                            for d in devs]
        doc["pcie_topology"]["host_bridges"][bridge] = entry
    return yaml.safe_dump(doc, sort_keys=False, width=120)


def compare(recorded: dict | None, rows: list[dict]) -> list[str]:
    """Differences between the recorded device->bridge mapping and the live one (names are informational)."""
    if not recorded or not recorded.get("host_bridges"):
        return ["no pcie_topology recorded (run: pcie_topology.py --record)"]
    rec = {(d["pci"], d["kind"]): b for b, v in recorded["host_bridges"].items() for d in (v.get("devices") or [])}
    live = {(r["pci"], r["kind"]): r["bridge"] for r in rows}
    problems = []
    for key in sorted(set(rec) | set(live)):
        if key not in live:
            problems.append(f"{key[1]} {key[0]} recorded under bridge {rec[key]} is gone")
        elif key not in rec:
            problems.append(f"{key[1]} {key[0]} under bridge {live[key]} is not recorded")
        elif rec[key] != live[key]:
            problems.append(f"{key[1]} {key[0]} moved: recorded bridge {rec[key]}, live {live[key]}")
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
