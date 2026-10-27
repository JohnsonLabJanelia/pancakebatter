#!/usr/bin/python3 -I
"""Root-side NVMe SMART snapshot for rig_health_check.py.

/dev/nvmeN is root-only (0600) and the rig-health unit runs unprivileged with NoNewPrivileges, so
it cannot read SMART itself. This script runs as root from nvme-smart-snapshot.timer (see
install_nvme_smart_snapshot.sh), reads `nvme smart-log /dev/nvmeN -o json` for every controller in
/sys/class/nvme, and writes one world-readable JSON document that the health check reads.

It takes no arguments, never writes to a device (smart-log is a read of log page 02h), and writes
only SNAPSHOT (atomically, via a temp file in the same directory). Stdlib only and self-contained:
the installed copy cannot import from the repo. tests/test_nvme_smart_snapshot.py fails if it gains
other write or exec paths.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

SYS_NVME = "/sys/class/nvme"
NVME = "/usr/sbin/nvme"
SNAPSHOT = "/var/lib/pancakebatter-nvme/smart.json"
SCHEMA = "pancakebatter.nvme_smart_snapshot.v1"
CTRL_RE = re.compile(r"^nvme\d+$")
NS_RE = re.compile(r"^nvme\d+n\d+$")
SAFE_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C"}


def read_attr(path):
    try:
        with open(path) as fh:
            return fh.read().strip()
    except OSError:
        return None


def controllers(sys_nvme=SYS_NVME):
    try:
        names = sorted(n for n in os.listdir(sys_nvme) if CTRL_RE.match(n))
    except OSError:
        return []
    return names


def smart_log(ctrl, nvme=NVME, run=subprocess.run):
    """(smart dict, None) or (None, error text). ctrl is validated against CTRL_RE before use."""
    if not CTRL_RE.match(ctrl):
        return None, f"refusing controller name {ctrl!r}"
    try:
        res = run([nvme, "smart-log", f"/dev/{ctrl}", "-o", "json"], capture_output=True, text=True,
                  timeout=15, env=SAFE_ENV)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, str(exc)
    if res.returncode != 0:
        return None, (res.stderr or "").strip()[:300] or f"nvme exited {res.returncode}"
    try:
        data = json.loads(res.stdout)
    except ValueError as exc:
        return None, f"unparseable smart-log output: {exc}"
    return (data, None) if isinstance(data, dict) else (None, "smart-log output is not an object")


def collect(sys_nvme=SYS_NVME, nvme=NVME, run=subprocess.run, now=None):
    out = {"schema": SCHEMA, "time": (now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
           "controllers": {}}
    for ctrl in controllers(sys_nvme):
        base = os.path.join(sys_nvme, ctrl)
        try:
            namespaces = sorted(n for n in os.listdir(base) if NS_RE.match(n))
        except OSError:
            namespaces = []
        smart, error = smart_log(ctrl, nvme, run)
        out["controllers"][ctrl] = {
            "model": read_attr(os.path.join(base, "model")),
            "serial": read_attr(os.path.join(base, "serial")),
            "firmware_rev": read_attr(os.path.join(base, "firmware_rev")),
            "namespaces": namespaces,
            "smart": smart,
            "error": error,
        }
    return out


def write_snapshot(doc, path=SNAPSHOT):
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".smart.", dir=directory)
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(doc, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(argv):
    if len(argv) > 1:
        print("usage: nvme_smart_snapshot (no arguments)", file=sys.stderr)
        return 2
    doc = collect()
    write_snapshot(doc)
    bad = [c for c, v in doc["controllers"].items() if v["error"]]
    print(f"nvme_smart_snapshot: {len(doc['controllers'])} controllers"
          + (f"; smart-log failed on {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
