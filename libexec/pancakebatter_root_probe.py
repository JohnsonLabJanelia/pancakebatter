#!/usr/bin/python3 -I
"""Read-only privileged probe for capture_inventory.py.

Installed root-owned (see install_root_probe.sh) and allowed via a sudoers rule that
permits this exact path with NO arguments. Reads, per physical NIC:
  * transceiver EEPROM text from `ethtool -m <iface>`
  * raw PCI VPD bytes (NIC part/serial number) from sysfs
and, once, the firmware's PCIe slot table from `dmidecode -t slot`, then prints
everything as JSON. It never writes anything, never takes input, and never runs
anything except `ethtool -m <validated iface>` and `dmidecode -t slot`.

Keep it read-only: tests/test_root_probe.py fails if it gains write or exec paths.
Stdlib only and self-contained (the installed copy cannot import from the repo).
"""
import json
import os
import re
import subprocess
import sys

SYS_NET = "/sys/class/net"
ETHTOOL = "/usr/sbin/ethtool"
DMIDECODE = "/usr/sbin/dmidecode"
IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def collect_slots(dmidecode=DMIDECODE, run=subprocess.run):
    try:
        res = run([dmidecode, "-t", "slot"], capture_output=True, text=True, timeout=15,
                  env=SAFE_ENV, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return res.stdout if res.returncode == 0 else None


def collect(sys_net=SYS_NET, ethtool=ETHTOOL, run=subprocess.run):
    out = {}
    for name in sorted(os.listdir(sys_net)):
        dev = os.path.join(sys_net, name, "device")
        if not IFACE_RE.match(name) or not os.path.exists(dev) or os.path.exists(os.path.join(sys_net, name, "wireless")):
            continue
        entry = {"ethtool_m": None, "vpd_hex": None}
        try:
            res = run([ethtool, "-m", name], capture_output=True, text=True, timeout=15,
                      env=SAFE_ENV, check=False)
            if res.returncode == 0:
                entry["ethtool_m"] = res.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            with open(os.path.join(dev, "vpd"), "rb") as f:
                entry["vpd_hex"] = f.read(4096).hex()
        except OSError:
            pass
        out[name] = entry
    return out


def main():
    if os.geteuid() != 0:
        print("must run as root (via sudo)", file=sys.stderr)
        return 1
    json.dump({"nics": collect(), "dmi_slots": collect_slots()}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
