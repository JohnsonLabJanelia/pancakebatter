#!/usr/bin/env python3
"""Status and in-place firmware reset for the ConnectX camera NICs.

  ./reset_camera_nics.py                 each camera card: PCI functions, netdevs, carrier, kernel health errors
  ./reset_camera_nics.py --json          the same as JSON (rig_health_check.py imports this module instead)
  sudo ./reset_camera_nics.py --reset [--card 61:00 ...] [--method auto|mlxfwreset|pci] [--restart-ptp] [--force]
  sudo ./reset_camera_nics.py --reset --dry-run     print what would run; change nothing

Exit status: 0 when every card has all of its expected netdevs, 1 when one does not,
2 when a reset was refused or did not bring the card back.

Why: on 2026-10-03 at 05:56 both ConnectX-7 cards on pancake0 reported "High
temperature" to the driver; by 05:58 (slot 49) and 06:00 (slot 61) the firmware
had halted (synd 0x10, severity CRITICAL), the driver's own recovery aborted
("PCI reads still not working") and all eight mlnx*_p*_25g netdevs vanished.
The cards still answered on the PCI bus, so a firmware reset through MFT
(mlxfwreset level 3: driver restart + PCI reset) is the fix that does not need
a host reboot. The PCI fallback does by hand what level 3 does: remove the
functions, pulse Secondary Bus Reset on the upstream bridge, rescan.

What it refuses to do: reset while Orange is recording (pgrep targets/release/orange)
unless --force; reset a healthy card unless you name it with --card.

After the netdevs return, the udev rules restore the mlnx names and
NetworkManager re-activates the static profiles (autoconnect=yes; the tool
nudges any that lag). ptp4l/phc2sys must be restarted because their sockets
were bound to the dead devices: --restart-ptp relaunches them with their
original command lines, otherwise the recipe is printed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

import hostconfig

SCRIPT_DIR = Path(__file__).resolve().parent
LOG_DIR = SCRIPT_DIR / "logs" / "nic_reset"
PCI_DEVICES = Path("/sys/bus/pci/devices")
ORANGE_PATTERN = "targets/release/orange"
PTP_PROCESSES = ("ptp4l", "phc2sys")  # start order matters: phc2sys talks to ptp4l's socket
HEALTH_PATTERNS = (
    "temp_warn",
    "Health issue observed",
    "device's health compromised",
    "handling bad device",
    "health recovery flow aborted",
    "synd 0x",
)
FW_DEAD_PATTERNS = (  # the driver re-probed after the reset but the firmware never came up: BAR reads are all-ones
    "pre-initializing state, aborting",
    "firmware version: 65535.65535.65535",
)
POWER_CYCLE_ADVICE = ("firmware did not boot after the reset (BAR reads return 0xffffffff): the card needs power "
                      "removed. A soft reboot keeps PCIe standby power, so power the host off, wait 30 s, power on.")
CARD_RE = re.compile(r"^[0-9a-f]{2}:[0-9a-f]{2}$")
BRIDGE_RE = re.compile(r"^0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-7]$")
SBR_BIT = 0x40  # Bridge Control register, Secondary Bus Reset


def _text(data) -> str:
    return data.decode(errors="replace") if isinstance(data, bytes) else (data or "")


class System:
    """Everything that touches the host, so tests can replace it and --dry-run can narrate it.

    read/listdir/exists/realpath/run are reads and always happen; mutate/write/sleep are
    the state changes and are only printed under dry_run.
    """

    def __init__(self, dry_run: bool = False, log=print):
        self.dry_run = dry_run
        self.log = log

    def read(self, path) -> str | None:
        try:
            return Path(path).read_text().strip()
        except OSError:
            return None

    def listdir(self, path) -> list[str]:
        try:
            return sorted(os.listdir(path))
        except OSError:
            return []

    def exists(self, path) -> bool:
        return Path(path).exists()

    def realpath(self, path) -> str:
        return os.path.realpath(path)

    def run(self, cmd: list[str], timeout: float = 120, input: str | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, input=input)
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(cmd, 127, "", str(exc))
        except subprocess.TimeoutExpired as exc:
            # TimeoutExpired carries the partial output as bytes even in text mode
            return subprocess.CompletedProcess(cmd, 124, _text(exc.stdout), f"timed out after {timeout}s; {_text(exc.stderr)}".strip("; "))

    def mutate(self, cmd: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
        self.log(f"+ {shlex.join(cmd)}")
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return self.run(cmd, timeout=timeout)

    def mutate_watching(self, cmd: list[str], timeout: float, abort_if=None, interval: float = 10) -> subprocess.CompletedProcess:
        """mutate(), but call abort_if() every `interval` seconds and kill the command once it returns true."""
        self.log(f"+ {shlex.join(cmd)}")
        if self.dry_run:
            return subprocess.CompletedProcess(cmd, 0, "", "")
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        except FileNotFoundError as exc:
            return subprocess.CompletedProcess(cmd, 127, "", str(exc))
        deadline = time.monotonic() + timeout
        while True:
            try:
                out, err = proc.communicate(timeout=max(1.0, min(interval, deadline - time.monotonic())))
                return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
            except subprocess.TimeoutExpired:
                pass
            reason = None
            if abort_if and abort_if():
                reason = "aborted: the firmware is not booting"
            elif time.monotonic() >= deadline:
                reason = f"timed out after {timeout}s"
            if reason:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)   # the whole group: mlxfwreset spawns helpers that hold the pipes
                except OSError:
                    proc.kill()
                out, err = proc.communicate()
                return subprocess.CompletedProcess(cmd, 124, _text(out), f"{reason}; {_text(err)}".strip("; "))

    def write(self, path, text: str) -> None:
        self.log(f"+ echo {text} > {path}")
        if not self.dry_run:
            Path(path).write_text(text)

    def spawn(self, argv: list[str], log_path: Path) -> None:
        self.log(f"+ {shlex.join(argv)} >> {log_path} &")
        if self.dry_run:
            return
        with open(log_path, "ab") as fh:
            subprocess.Popen(argv, stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             start_new_session=True)

    def sleep(self, seconds: float) -> None:
        if not self.dry_run:
            time.sleep(seconds)

    def now(self) -> float:
        return time.monotonic()

    def is_root(self) -> bool:
        return os.geteuid() == 0


# --- config -------------------------------------------------------------------------------

def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load(Path(path or hostconfig.default_config_path()).read_text()) or {}


def mellanox_nics(config: dict) -> dict[str, dict]:
    """name -> facts for every NIC with a PCI id (camera and spare alike: a card resets as a whole)."""
    out: dict[str, dict] = {}
    for entry in config.get("nics") or []:
        for name, nic in (entry or {}).items():
            nic = nic or {}
            pcie = str(nic.get("pcie_id") or "").lower()
            if not pcie:
                continue
            out[name] = {
                "pcie_id": pcie,
                "role": nic.get("role"),
                "managed": bool(nic.get("managed")),
                "expected_link": bool(nic.get("expected_link")),
                "mac": str(nic.get("mac_address") or "").lower(),
            }
    return out


def card_of(pcie_id: str) -> str:
    """'61:00.1' -> '61:00' (one physical card = one PCI device, functions .0-.3)."""
    return pcie_id.rsplit(".", 1)[0]


def cards(nics: dict[str, dict]) -> dict[str, dict[str, str]]:
    """card -> {pcie_id: netdev name}."""
    grouped: dict[str, dict[str, str]] = {}
    for name, nic in nics.items():
        grouped.setdefault(card_of(nic["pcie_id"]), {})[nic["pcie_id"]] = name
    return {card: dict(sorted(fns.items())) for card, fns in sorted(grouped.items())}


# --- observation ----------------------------------------------------------------------------

def pci_path(pcie_id: str) -> Path:
    return PCI_DEVICES / f"0000:{pcie_id}"


def function_state(sys_: System, pcie_id: str, expected_netdev: str) -> dict:
    dev = pci_path(pcie_id)
    netdevs = sys_.listdir(dev / "net")
    netdev = netdevs[0] if netdevs else None
    driver = os.path.basename(sys_.realpath(dev / "driver")) if sys_.exists(dev / "driver") else None
    carrier = sys_.read(f"/sys/class/net/{netdev}/carrier") if netdev else None
    return {
        "pcie_id": pcie_id,
        "expected_netdev": expected_netdev,
        "pci_present": sys_.exists(dev),
        "driver": driver,
        "netdev": netdev,
        "carrier": (carrier == "1") if carrier is not None else None,
        "operstate": sys_.read(f"/sys/class/net/{netdev}/operstate") if netdev else None,
    }


def journal_time(value: str) -> str:
    """journalctl --since wants 'YYYY-MM-DD HH:MM:SS'; systemd 249 rejects the ISO 'T' form
    (and any UTC offset) that datetime.isoformat() produces. Relative forms like '-15min' pass through."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", value)
    return f"{m.group(1)} {m.group(2)}" if m else value


def kernel_journal(sys_: System, since: str | None = None) -> tuple[bool, list[str]]:
    """(ok, lines) of the kernel log for this boot, or since a time. ok is False when journalctl failed,
    so callers can tell 'no messages' from 'could not look'."""
    cmd = ["journalctl", "-k", "-o", "short-iso", "--no-pager"]
    cmd += ["--since", journal_time(since)] if since else ["-b"]
    res = sys_.run(cmd, timeout=60)
    return res.returncode == 0, (res.stdout.splitlines() if res.returncode == 0 else [(res.stderr or "").strip()])


def kernel_health_lines(sys_: System, pcie_ids: list[str], since: str | None = None) -> dict[str, list[str]]:
    """mlx5 health/temperature kernel messages per PCI function (this boot, or --since)."""
    ok, lines = kernel_journal(sys_, since)
    out: dict[str, list[str]] = {p: [] for p in pcie_ids}
    if not ok:
        return out
    for line in lines:
        if "mlx5_core" not in line or not any(pat in line for pat in HEALTH_PATTERNS):
            continue
        for p in pcie_ids:
            if f"mlx5_core 0000:{p}:" in line:
                out[p].append(line.strip())
                break
    return out


def firmware_dead(sys_: System, pcie_ids: list[str], since: str) -> list[str]:
    """Kernel lines since `since` showing a function whose firmware never initialised after the reset."""
    ok, lines = kernel_journal(sys_, since)
    if not ok:
        return []
    return [line.strip() for line in lines
            if any(f"mlx5_core 0000:{p}:" in line for p in pcie_ids) and any(pat in line for pat in FW_DEAD_PATTERNS)]


def orange_pids(sys_: System) -> list[int]:
    """PIDs whose executable is Orange (argv[0] check, so a shell merely mentioning the path does not count)."""
    res = sys_.run(["pgrep", "-f", ORANGE_PATTERN])
    pids = []
    for pid in (res.stdout.split() if res.returncode == 0 else []):
        argv0 = (sys_.read(f"/proc/{pid}/cmdline") or "").split("\0")[0]
        if argv0.endswith(ORANGE_PATTERN) or os.path.basename(argv0) == "orange":
            pids.append(int(pid))
    return pids


def ptp_processes(sys_: System) -> list[dict]:
    """Running ptp4l/phc2sys with argv and, when systemd owns them, the unit name (from the cgroup)."""
    procs = []
    for name in PTP_PROCESSES:
        res = sys_.run(["pgrep", "-x", name])
        for pid in (res.stdout.split() if res.returncode == 0 else []):
            raw = sys_.read(f"/proc/{pid}/cmdline") or ""
            argv = [a for a in raw.split("\0") if a]
            cgroup = sys_.read(f"/proc/{pid}/cgroup") or ""
            m = re.search(r"/([^/]+\.service)\s*$", cgroup, re.M)
            unit = m.group(1) if m and m.group(1).startswith(("ptp4l", "phc2sys")) else None
            procs.append({"name": name, "pid": int(pid), "argv": argv, "unit": unit})
    return procs


def card_report(sys_: System, card: str, functions: dict[str, str], nics: dict[str, dict],
                health: dict[str, list[str]]) -> dict:
    states = [function_state(sys_, p, functions[p]) for p in functions]
    missing = [s["expected_netdev"] for s in states if not s["netdev"] or not s["pci_present"]]
    misnamed = [f"{s['netdev']} (expected {s['expected_netdev']})" for s in states
                if s["netdev"] and s["netdev"] != s["expected_netdev"]]
    return {
        "card": card,
        "has_camera": any(nics[n]["role"] == "camera" for n in functions.values()),
        "functions": states,
        "missing_netdevs": missing,
        "misnamed_netdevs": misnamed,
        "health_errors": {p: health.get(p, [])[-6:] for p in functions},
        "healthy": not missing and not misnamed,
    }


def status(sys_: System, config: dict) -> dict:
    nics = mellanox_nics(config)
    grouped = cards(nics)
    health = kernel_health_lines(sys_, [n["pcie_id"] for n in nics.values()])
    reports = [card_report(sys_, card, fns, nics, health) for card, fns in grouped.items()]
    return {
        "host": socket.gethostname().split(".")[0],
        "generated": datetime.now().isoformat(timespec="seconds"),
        "orange_pids": orange_pids(sys_),
        "ptp": ptp_processes(sys_),
        "cards": reports,
        "healthy": all(r["healthy"] for r in reports),
    }


def format_status(report: dict) -> str:
    lines = [f"camera NICs on {report['host']} at {report['generated']}"]
    for card in report["cards"]:
        tag = "OK" if card["healthy"] else "UNHEALTHY"
        lines.append(f"  card {card['card']}  [{tag}]{'  (camera)' if card['has_camera'] else ''}")
        for fn in card["functions"]:
            if not fn["pci_present"]:
                state = "PCI function missing"
            elif not fn["netdev"]:
                state = f"no netdev (driver {fn['driver'] or 'unbound'})"
            else:
                link = "link up" if fn["carrier"] else ("no carrier" if fn["carrier"] is False else "carrier unknown")
                state = f"{fn['netdev']}  {link}  ({fn['operstate']})"
                if fn["netdev"] != fn["expected_netdev"]:
                    state += f"  NAME MISMATCH: expected {fn['expected_netdev']}"
            lines.append(f"    {fn['pcie_id']}  {fn['expected_netdev']:<14} {state}")
        errors = [l for ls in card["health_errors"].values() for l in ls]
        if errors:
            lines.append(f"    kernel health messages this boot ({len(errors)}), last:")
            lines.append(f"      {errors[-1]}")
    if report["orange_pids"]:
        lines.append(f"  Orange is running (pids {', '.join(map(str, report['orange_pids']))})")
    for proc in report["ptp"]:
        owner = f" [{proc['unit']}]" if proc.get("unit") else ""
        lines.append(f"  {proc['name']} pid {proc['pid']}{owner}: {shlex.join(proc['argv'])}")
    return "\n".join(lines)


# --- reset ------------------------------------------------------------------------------------

def parse_mlxfwreset_levels(output: str) -> dict[int, bool]:
    """'Reset-levels:' section of `mlxfwreset query` -> {level: supported}."""
    levels: dict[int, bool] = {}
    in_levels = False
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("reset-levels"):
            in_levels = True
            continue
        if stripped.lower().startswith("reset-") and in_levels:
            break
        if not in_levels:
            continue
        m = re.match(r"^(\d+):\s*(.*?)\s*-\s*(Supported|Not Supported)", stripped)
        if m:
            levels[int(m.group(1))] = m.group(3) == "Supported"
    return levels


def reset_card_mlxfwreset(sys_: System, card: str, functions: dict[str, str] | None = None,
                          started: str | None = None) -> tuple[bool, str]:
    """mlxfwreset level 3; while it runs, watch the kernel log and give up early if the firmware never boots."""
    dev = f"{card}.0"
    sys_.mutate(["mst", "start"])  # loads the mst modules; harmless if already running
    query = sys_.run(["mlxfwreset", "-d", dev, "query"], timeout=180)
    levels = parse_mlxfwreset_levels(query.stdout) if query.returncode == 0 else {}
    if query.returncode != 0:
        return False, f"mlxfwreset query failed (rc {query.returncode}): {(query.stderr or query.stdout).strip()[-300:]}"
    if not levels.get(3):
        return False, f"mlxfwreset level 3 not supported on {dev} (levels: {levels})"
    abort_if = (lambda: bool(firmware_dead(sys_, list(functions), started))) if functions and started else None
    res = sys_.mutate_watching(["mlxfwreset", "-d", dev, "--level", "3", "reset", "-y"], timeout=600, abort_if=abort_if)
    tail = (_text(res.stdout) + _text(res.stderr)).strip()[-600:]
    return res.returncode == 0, f"mlxfwreset level 3 on {dev} rc {res.returncode}: {tail}"


def upstream_bridge(sys_: System, pcie_id: str) -> str:
    return os.path.basename(sys_.realpath(pci_path(pcie_id) / ".."))


def reset_card_pci(sys_: System, card: str, functions: dict[str, str]) -> tuple[bool, str]:
    """Remove the functions, pulse Secondary Bus Reset on the upstream bridge, rescan."""
    bridge = upstream_bridge(sys_, f"{card}.0")
    if not BRIDGE_RE.match(bridge):
        return False, f"unexpected upstream device {bridge!r} for {card}; refusing bus reset"
    ctl = sys_.run(["setpci", "-s", bridge, "BRIDGE_CONTROL"])
    if ctl.returncode != 0 or not re.fullmatch(r"[0-9a-fA-F]{4}", ctl.stdout.strip()):
        return False, f"could not read BRIDGE_CONTROL of {bridge}: {(ctl.stderr or ctl.stdout).strip()}"
    value = int(ctl.stdout.strip(), 16)
    for pcie_id in sorted(functions, reverse=True):
        if sys_.exists(pci_path(pcie_id)):
            sys_.write(pci_path(pcie_id) / "remove", "1")
    sys_.sleep(1)
    sys_.mutate(["setpci", "-s", bridge, f"BRIDGE_CONTROL={value | SBR_BIT:04x}"])
    sys_.sleep(0.2)   # spec minimum is 1 ms
    sys_.mutate(["setpci", "-s", bridge, f"BRIDGE_CONTROL={value & ~SBR_BIT:04x}"])
    sys_.sleep(3)     # spec minimum 100 ms; the NIC firmware needs a few seconds to boot
    sys_.write(PCI_DEVICES / bridge / "rescan", "1")
    return True, f"secondary bus reset on {bridge}, rescanned"


def wait_for_netdevs(sys_: System, functions: dict[str, str], timeout: float) -> tuple[bool, list[dict]]:
    deadline = sys_.now() + timeout
    while True:
        states = [function_state(sys_, p, functions[p]) for p in functions]
        if all(s["netdev"] == s["expected_netdev"] for s in states):
            return True, states
        if sys_.dry_run or sys_.now() >= deadline:
            return False, states
        sys_.sleep(2)


def reconnect_nm(sys_: System, names: list[str]) -> None:
    for name in names:
        state = sys_.run(["nmcli", "-g", "GENERAL.STATE", "device", "show", name])
        if state.returncode == 0 and state.stdout.strip().startswith("100"):
            continue
        res = sys_.mutate(["nmcli", "connection", "up", name], timeout=60)
        if res.returncode != 0:
            sys_.log(f"  nmcli connection up {name}: rc {res.returncode} {res.stderr.strip()[-200:]}")


def ptp_units(procs: list[dict]) -> list[str]:
    """systemd units behind the running daemons, ptp4l first (phc2sys is PartOf= it, but be explicit)."""
    units = {p["unit"] for p in procs if p.get("unit")}
    return sorted(units, key=lambda u: (not u.startswith("ptp4l"), u))


def restart_ptp(sys_: System, procs: list[dict], log_dir: Path) -> None:
    units = ptp_units(procs)
    if units:
        sys_.mutate(["systemctl", "restart", *units], timeout=120)
    manual = sorted((p for p in procs if not p.get("unit")), key=lambda p: PTP_PROCESSES.index(p["name"]))
    if not manual:
        return
    for proc in manual:
        sys_.mutate(["kill", str(proc["pid"])])
    sys_.sleep(2)
    for proc in manual:
        sys_.spawn(proc["argv"], log_dir / f"{proc['name']}.log")
        sys_.sleep(1)


def ptp_recipe(procs: list[dict]) -> list[str]:
    units = ptp_units(procs)
    lines = [f"sudo systemctl restart {' '.join(units)}"] if units else []
    manual = sorted((p for p in procs if not p.get("unit")), key=lambda p: PTP_PROCESSES.index(p["name"]))
    if manual:
        lines.append(f"sudo kill {' '.join(str(p['pid']) for p in manual)}")
        lines += [f"sudo -b {shlex.join(p['argv'])}" for p in manual]
    return lines


class Log:
    def __init__(self, path: Path | None):
        self.path = path
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = path.open("a")
        else:
            self.fh = None

    def __call__(self, line: str = "") -> None:
        print(line, flush=True)
        if self.fh:
            self.fh.write(f"{datetime.now().isoformat(timespec='seconds')} {line}\n")
            self.fh.flush()


def do_reset(sys_: System, config: dict, report: dict, args, log) -> int:
    if not sys_.is_root() and not args.dry_run:
        log("--reset needs root (sudo ./reset_camera_nics.py --reset)")
        return 2
    if report["orange_pids"] and not args.force:
        log(f"Orange is running (pids {report['orange_pids']}); stop the recording first or pass --force")
        return 2
    nics = mellanox_nics(config)
    grouped = cards(nics)
    if args.card:
        unknown = [c for c in args.card if c not in grouped]
        if unknown:
            log(f"unknown card(s) {unknown}; configured cards: {list(grouped)}")
            return 2
        targets = [c for c in report["cards"] if c["card"] in args.card]
    else:
        targets = [c for c in report["cards"] if not c["healthy"]]
    if not targets:
        log("every card already has its netdevs; nothing to reset (name one with --card to force it)")
        return 0

    for card in targets:
        functions = grouped[card["card"]]
        log(f"--- card {card['card']}: missing {card['missing_netdevs'] or 'none'}; method {args.method}")
        for pcie_id, lines in card["health_errors"].items():
            for line in lines:
                log(f"    pre-reset {pcie_id}: {line}")
        started = datetime.now().isoformat(timespec="seconds")
        ok, msg = False, "skipped"
        came_back = False
        for method in [m for m in ("mlxfwreset", "pci") if args.method in ("auto", m)]:
            if method == "mlxfwreset":
                ok, msg = reset_card_mlxfwreset(sys_, card["card"], functions, started)
            else:
                ok, msg = reset_card_pci(sys_, card["card"], functions)
            log(f"    {msg}")
            if ok:
                came_back, states = wait_for_netdevs(sys_, functions, args.wait)
                for st in states:
                    log(f"    {st['pcie_id']} -> {st['netdev'] or 'no netdev'} (expected {st['expected_netdev']})")
                if came_back:
                    break
                log(f"    netdevs did not all return within {args.wait}s" + (" (dry run)" if args.dry_run else ""))
            dead = [] if args.dry_run else firmware_dead(sys_, list(functions), started)
            if dead:
                log(f"    {dead[-1]}")
                log(f"    {POWER_CYCLE_ADVICE}")
                ok = False
                break   # the PCI route re-probes the same dead firmware; do not bother
        if not ok or not came_back:
            log(f"    card {card['card']} is still down" + ("" if ok else "; a host power cycle is the remaining option"))
            continue

    managed = [n for n, nic in nics.items() if nic["managed"] and card_of(nic["pcie_id"]) in {c["card"] for c in targets}]
    if managed:
        sys_.sleep(3)
        log("--- NetworkManager")
        reconnect_nm(sys_, managed)

    if report["ptp"]:
        log("--- PTP")
        if args.restart_ptp:
            log_path = getattr(log, "path", None)
            restart_ptp(sys_, report["ptp"], log_path.parent if log_path else LOG_DIR)
        else:
            log("ptp4l/phc2sys were bound to the old devices; restart them (or rerun with --restart-ptp):")
            for line in ptp_recipe(report["ptp"]):
                log(f"    {line}")

    final = status(sys_, config)
    log("--- after")
    log(format_status(final))
    if args.dry_run:
        return 0
    return 0 if final["healthy"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter,
                                     epilog=__doc__)
    parser.add_argument("--config", type=Path, help="host config (default hosts/<hostname>/config.yml)")
    parser.add_argument("--json", action="store_true", help="print the status as JSON")
    parser.add_argument("--reset", action="store_true", help="reset unhealthy cards (or the ones named with --card)")
    parser.add_argument("--card", action="append", metavar="BB:DD", help="card to reset, e.g. 61:00 (repeatable)")
    parser.add_argument("--method", choices=("auto", "mlxfwreset", "pci"), default="auto",
                        help="auto tries mlxfwreset level 3, then the PCI bus reset")
    parser.add_argument("--wait", type=float, default=90, help="seconds to wait for the netdevs to return")
    parser.add_argument("--restart-ptp", action="store_true", help="kill and relaunch ptp4l/phc2sys after the reset")
    parser.add_argument("--force", action="store_true", help="reset even while Orange is running")
    parser.add_argument("--dry-run", action="store_true", help="with --reset: print the commands only")
    args = parser.parse_args(argv)
    if args.card:
        args.card = [c.lower() for c in args.card]
        bad = [c for c in args.card if not CARD_RE.match(c)]
        if bad:
            parser.error(f"--card expects BB:DD like 61:00, got {bad}")

    config = load_config(args.config)
    log = Log(LOG_DIR / f"nic_reset_{datetime.now():%Y%m%d_%H%M%S}.log") if args.reset and not args.dry_run else Log(None)
    sys_ = System(dry_run=args.dry_run, log=log)
    report = status(sys_, config)

    if args.json and not args.reset:
        print(json.dumps(report, indent=2))
        return 0 if report["healthy"] else 1
    log(format_status(report))
    if not args.reset:
        return 0 if report["healthy"] else 1
    log()
    return do_reset(sys_, config, report, args, log)


if __name__ == "__main__":
    sys.exit(main())
