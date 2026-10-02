#!/usr/bin/env python3
"""Turn a captured config into a pancake0-style NIC layout.

Reads hosts/<hostname>/config.captured.yml, renames the NIC cards you identify using
the pancake0 convention, and writes hosts/<hostname>/config.yml (refuses to overwrite).

Convention (see docs/system_config_v1.md):
  name     mlnx<card>_p<port>_<speed>g     e.g. mlnx1_p1_25g
  port     PCIe function + 1                (p1 = function 0)
  altname  eth0, eth1, ...                  in card/port order
  ip       192.168.<110 + 10*n>.1/24        one subnet per port, n in card/port order
  card     1 = physically nearest the CPU; confirm with `sudo ethtool -p <iface> 20`
NICs not named with --card (onboard/management ports) keep their name and role.

Example (dumpling: card 1 at PCIe f1:00, card 2 at 21:00, four cameras on card 1):
    ./suggest_nic_layout.py --card f1:00=1 --card 21:00=2 --camera-card 1
    ./suggest_nic_layout.py --card f1:00=1 --card 21:00=2 --camera 1.1 --camera 1.2

Review the result in ./gui_config_editor.py, then use network_alias_assignment.sh --dry-run.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

import yaml

import capture_inventory
import hostconfig

HEADER = """\
# Proposed by suggest_nic_layout.py from config.captured.yml -- review before use.
# NIC names/IPs follow the pancake0 convention; cameras, pdus, kernel_tuning are still yours to fill in.
"""


class LayoutError(Exception):
    pass


def parse_card_arg(value: str) -> tuple[str, int]:
    m = re.fullmatch(r"([0-9a-fA-F]{2}:[0-9a-fA-F]{2})=(\d+)", value)
    if not m or int(m.group(2)) < 1:
        raise argparse.ArgumentTypeError(f"--card expects BUS:DEV=N (e.g. f1:00=1), got {value!r}")
    return m.group(1).lower(), int(m.group(2))


def parse_camera_arg(value: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+)\.(\d+)", value)
    if not m:
        raise argparse.ArgumentTypeError(f"--camera expects CARD.PORT (e.g. 1.1), got {value!r}")
    return int(m.group(1)), int(m.group(2))


def build_layout(config: dict[str, Any], cards: dict[str, int], camera_ports: set[tuple[int, int]],
                 camera_cards: set[int], speed_g: int = 25, subnet_base: int = 110) -> dict[str, Any]:
    if len(set(cards.values())) != len(cards):
        raise LayoutError("each --card number may be used for only one bus")
    others: list[dict[str, Any]] = []
    layout: list[tuple[int, int, str, dict[str, Any]]] = []
    seen_buses = set()
    for item in config.get("nics", []):
        (old_name, nic), = item.items()
        pcie = str(nic.get("pcie_id") or "").lower()
        bus = pcie[:5]
        if bus in cards:
            seen_buses.add(bus)
            layout.append((cards[bus], int(pcie[-1]) + 1, old_name, dict(nic)))
        else:
            others.append(item)
    missing = set(cards) - seen_buses
    if missing:
        raise LayoutError(f"no NIC found at PCIe bus(es): {', '.join(sorted(missing))}")
    layout.sort(key=lambda t: (t[0], t[1]))
    existing = {(c, p) for c, p, _, _ in layout}
    bad_ports = sorted(camera_ports - existing)
    bad_cards = sorted(camera_cards - {c for c, _ in existing})
    if bad_ports or bad_cards:
        raise LayoutError(f"camera selection matches no NIC: ports {bad_ports} cards {bad_cards}")

    new_nics = list(others)
    for index, (card, port, _old, nic) in enumerate(layout):
        is_camera = (card, port) in camera_ports or card in camera_cards
        nic.update({
            "card": card,
            "altname": f"eth{index}",
            "role": "camera" if is_camera else "spare",
            "managed": is_camera,
            "expected_link": is_camera,
            "mtu": 9000,
            "ip_address": f"192.168.{subnet_base + 10 * index}.1/24",
            "link_settings": {"speed": speed_g * 1000, "autoneg": True},
        })
        new_nics.append({f"mlnx{card}_p{port}_{speed_g}g": nic})
    out = dict(config)
    out["nics"] = new_nics
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--card", action="append", type=parse_card_arg, required=True, metavar="BUS:DEV=N",
                    help="NIC card at PCIe BUS:DEV is card N (repeatable)")
    ap.add_argument("--camera", action="append", type=parse_camera_arg, default=[], metavar="CARD.PORT",
                    help="this port gets a camera (repeatable)")
    ap.add_argument("--camera-card", action="append", type=int, default=[], metavar="N",
                    help="every port of card N gets a camera (repeatable)")
    ap.add_argument("--speed", type=int, default=25, help="link speed in Gb/s (default 25)")
    ap.add_argument("--file", type=Path, help="default: hosts/<hostname>/config.captured.yml")
    ap.add_argument("--output", type=Path, help="default: hosts/<hostname>/config.yml")
    ap.add_argument("--force", action="store_true", help="overwrite the output file")
    args = ap.parse_args(argv)

    src = args.file or hostconfig.host_file("config.captured.yml")
    dest = args.output or hostconfig.host_file("config.yml")
    if dest.exists() and not args.force:
        raise SystemExit(f"{dest} exists; pass --output elsewhere or --force")
    try:
        config = yaml.safe_load(src.read_text())
        proposed = build_layout(config, dict(args.card), set(args.camera), set(args.camera_card), args.speed)
    except (OSError, LayoutError) as exc:
        raise SystemExit(str(exc))

    problems = capture_inventory.validate(proposed)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(HEADER + yaml.safe_dump(proposed, sort_keys=False, width=120))
    print(f"wrote {dest}", file=sys.stderr)
    for item in proposed["nics"]:
        (name, nic), = item.items()
        print(f"  {name:16} {nic['role']:10} {nic['ip_address']:18} pcie {nic['pcie_id']}", file=sys.stderr)
    for p in problems:
        print(f"schema: {p}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
