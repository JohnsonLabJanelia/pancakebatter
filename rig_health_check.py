#!/usr/bin/env python3
"""Read-only rig health check, meant to run every few minutes from a systemd timer.

  ./rig_health_check.py                  print findings; exit 0 ok, 1 warnings, 2 critical
  ./rig_health_check.py --json           one JSON document instead
  ./rig_health_check.py --color always   force colour (default: only on a terminal; rich if installed, else ANSI)
  ./rig_health_check.py --state-dir DIR  remember the last result there; level changes are what get mailed
  ./rig_health_check.py --print-affinity the non-isolated CPU list (install_rig_health_timer.sh uses it)
  ./rig_health_check.py --preflight --recording-root DIR [--expected-gb N] [--state-dir /var/lib/rig-health]
                                         pre-recording gate for the drive under DIR; exit 2 = do not record
  ./rig_health_check.py --state-dir /var/lib/rig-health --acknowledge-drive SERIAL [--note TEXT]
                                         mark a drive's current media/kernel errors as known

Checks (reads of sysfs, /proc and the journal, plus one nvidia-smi query):
  camera_nics   every mlnx netdev in hosts/<host>/config.yml exists (crit); expected_link ports have carrier (warn)
  mlx5_health   mlx5 temperature/health kernel messages since the last run (crit)
  nic_temps     ConnectX ASIC and transceiver temperatures from hwmon (warn/crit thresholds below; 0 C = warn, the
                reading the firmware gives once it has halted)
  cpu_temp      k10temp Tctl
  nvme_temps    NVMe composite against the drive's own max (warn) and crit
  gpu_temps     nvidia-smi temperature.gpu (--no-gpu skips it)
  disk_space    free space on / and the /mnt data partitions in the host config
  ptp           ptp4l and phc2sys alive when camera NICs are configured; phc2sys offset sane (--no-ptp skips it)
  time_sync     an NTP service is enabled and synchronized (timedatectl); system clock offset from one SNTP
                query (warn 1 s, crit 300 s; --no-ntp-query skips the packet)
  pcie_aer      new PCIe AER messages since the last run (warn)
  storage_errors  kernel "critical medium error", I/O errors, nvme timeouts/resets per drive since the last run;
                crit, and stays crit until the drive is acknowledged
  nvme_smart    SMART from the root snapshot (install_nvme_smart_snapshot.sh): critical_warning, media errors above
                the acknowledged baseline, spare at threshold (crit); new error-log entries, unsafe shutdowns,
                over-temperature minutes, endurance used (warn); a stale snapshot warns
  nvme_firmware same-model drives on different firmware, or firmware that differs from the host config (warn)
  acquisition   whether Orange is recording (info only)
  host_config   the installed copy other programs read (/etc/pancakebatter/host.yml) matches the checkout (warn if stale)
  recording_transfer  every sealer version under paths.recording_transfer_root matches releases.json, file by file

Staying out of the acquisition pipeline's way: the script renices itself to 19, pins itself to the
cores NOT in kernel_tuning.isolated_cores, writes nothing outside --state-dir, never touches the NIC
firmware or any device, and finishes in well under a second apart from nvidia-smi. The unit adds
IOSchedulingClass=idle and CPUSchedulingPolicy=idle (see systemd/rig-health.service.in).

Alerts: findings go to stdout (the journal under the timer). When a check changes level, and
RIG_HEALTH_EMAIL is set (see configs/rig-health.default), a short mail goes out through msmtp or
sendmail. Every run appends one JSON line to <state-dir>/history.jsonl.

Why (2026-10-03): both ConnectX-7 cards hit their firmware thermal cutoff at 05:56-06:00 and were
gone for two days before anyone noticed; the kernel had logged a temperature warning 90 seconds
before the halt. This check would have mailed at 06:00 and shown the cards missing ever since.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import yaml

import hostconfig
import reset_camera_nics as rcn

LEVEL_RANK = {"ok": 0, "info": 0, "warn": 1, "crit": 2}
THRESHOLDS = {  # degrees C unless noted; override with --threshold name=value
    "nic_warn": 85, "nic_crit": 95,          # ConnectX-7 ASIC; firmware halts around 105
    "module_warn": 65, "module_crit": 73,    # SFP28 optics, 70 C class parts
    "cpu_warn": 85, "cpu_crit": 95,
    "nvme_warn": 70, "nvme_crit": 80,        # used when the drive exposes no max/crit
    "gpu_warn": 85, "gpu_crit": 92,
    "disk_warn_pct": 10, "disk_crit_pct": 3, # free space
    "ptp_warn_ns": 100_000, "ptp_crit_ns": 10_000_000,
    "time_warn_s": 1, "time_crit_s": 300,     # system clock vs NTP
    "nvme_used_warn_pct": 80,                 # SMART percent_used (rated endurance)
    "smart_stale_s": 900,                     # SMART snapshot older than this: warn
    "preflight_margin_pct": 10,               # --preflight: free space must exceed --expected-gb by this much
}
NTP_SERVER = "pool.ntp.org"
HWMON = Path("/sys/class/hwmon")
MAIL_SUBJECT = "[rig-health] {host}: {summary}"


@dataclass
class Finding:
    check: str
    level: str
    message: str
    value: object = None


class Host(rcn.System):
    """rcn.System plus the few extra reads this script needs."""

    def statvfs(self, path):
        return os.statvfs(path)

    def sntp_offset(self, server: str, timeout: float = 3.0) -> float:
        """Seconds the system clock is behind `server` (negative = ahead); one 48-byte SNTP exchange."""
        import socket as _socket
        import struct
        import time as _time
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            t1 = _time.time()
            sock.sendto(b"\x1b" + 47 * b"\0", (server, 123))
            data, _ = sock.recvfrom(48)
            t4 = _time.time()
        finally:
            sock.close()
        fields = struct.unpack("!12I", data)
        ntp = lambda i: fields[i] - 2208988800 + fields[i + 1] / 2**32
        return ((ntp(8) - t1) + (ntp(10) - t4)) / 2

    def cpu_count(self) -> int:
        return os.cpu_count() or 1

    def stat_dev(self, path) -> tuple[int, int]:
        st = os.stat(path)
        return os.major(st.st_dev), os.minor(st.st_dev)


# --- cpu lists ----------------------------------------------------------------------------------

def parse_cpulist(text: str) -> set[int]:
    cpus: set[int] = set()
    for part in str(text or "").replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus


def format_cpulist(cpus) -> str:
    cpus = sorted(set(cpus))
    out, i = [], 0
    while i < len(cpus):
        j = i
        while j + 1 < len(cpus) and cpus[j + 1] == cpus[j] + 1:
            j += 1
        out.append(str(cpus[i]) if i == j else f"{cpus[i]}-{cpus[j]}")
        i = j + 1
    return " ".join(out)


def allowed_cpus(config: dict, ncpu: int) -> list[int]:
    isolated = parse_cpulist((config.get("kernel_tuning") or {}).get("isolated_cores", ""))
    allowed = [c for c in range(ncpu) if c not in isolated]
    return allowed or list(range(ncpu))


def stay_out_of_the_way(config: dict) -> None:
    try:
        os.nice(max(0, 19 - os.nice(0)))
    except OSError:
        pass
    try:
        os.sched_setaffinity(0, allowed_cpus(config, os.cpu_count() or 1))
    except (OSError, AttributeError):
        pass


# --- helpers ------------------------------------------------------------------------------------

def level_for(value: float, warn: float, crit: float) -> str:
    return "crit" if value >= crit else "warn" if value >= warn else "ok"


def hwmon_devices(host: Host) -> list[dict]:
    """[{path, name, device (pci id without domain or None), temps: [{label, input, max, crit}]}]."""
    out = []
    for entry in host.listdir(HWMON):
        path = HWMON / entry
        name = host.read(path / "name")
        if not name:
            continue
        device = os.path.basename(host.realpath(path / "device")) if host.exists(path / "device") else ""
        temps = []
        for attr in host.listdir(path):
            m = re.fullmatch(r"temp(\d+)_input", attr)
            if not m:
                continue
            raw = host.read(path / attr)
            if raw is None:
                continue
            idx = m.group(1)

            def milli(name_):
                v = host.read(path / f"temp{idx}_{name_}")
                return int(v) / 1000 if v and v.lstrip("-").isdigit() else None

            temps.append({"label": host.read(path / f"temp{idx}_label") or f"temp{idx}",
                          "input": int(raw) / 1000 if raw.lstrip("-").isdigit() else None,
                          "max": milli("max"), "crit": milli("crit")})
        out.append({"path": str(path), "name": name,
                    "device": device[5:] if device.startswith("0000:") else device, "temps": temps})
    return out


# --- checks -------------------------------------------------------------------------------------

def check_camera_nics(host: Host, config: dict) -> list[Finding]:
    nics = rcn.mellanox_nics(config)
    if not nics:
        return [Finding("camera_nics", "info", "no NICs with a pcie_id in the host config")]
    findings = []
    missing_by_card: dict[str, list[str]] = {}
    for name, nic in nics.items():
        st = rcn.function_state(host, nic["pcie_id"], name)
        if not st["netdev"] or not st["pci_present"]:
            missing_by_card.setdefault(rcn.card_of(nic["pcie_id"]), []).append(name)
        elif st["netdev"] != name:
            findings.append(Finding("camera_nics", "warn", f"{nic['pcie_id']} is named {st['netdev']}, expected {name}"))
        elif nic["expected_link"] and st["carrier"] is False:
            findings.append(Finding("camera_nics", "warn", f"{name}: no carrier (camera or fiber down?)"))
    for card, names in sorted(missing_by_card.items()):
        findings.append(Finding("camera_nics", "crit",
                                f"card {card}: netdevs missing: {', '.join(names)} (reset_camera_nics.py)", names))
    return findings or [Finding("camera_nics", "ok", f"{len(nics)} netdevs present")]


def check_mlx5_health(host: Host, config: dict, since: str) -> list[Finding]:
    nics = rcn.mellanox_nics(config)
    ok, raw = rcn.kernel_journal(host, since)
    if not ok:
        return [Finding("mlx5_health", "warn", f"could not read the kernel log: {' '.join(raw)[:120]}")]
    lines = rcn.kernel_health_lines(host, [n["pcie_id"] for n in nics.values()], since=since)
    hits = [l for ls in lines.values() for l in ls]
    if not hits:
        return [Finding("mlx5_health", "ok", f"no mlx5 health messages since {since}")]
    return [Finding("mlx5_health", "crit", f"{len(hits)} mlx5 health/temperature messages since {since}; last: {hits[-1]}", len(hits))]


def check_temperatures(host: Host, config: dict, th: dict, with_gpu: bool) -> list[Finding]:
    by_pcie = {nic["pcie_id"]: name for name, nic in rcn.mellanox_nics(config).items()}
    findings = []
    dead_by_card: dict[str, list[str]] = {}
    for dev in hwmon_devices(host):
        if dev["name"] == "mlx5":
            label = by_pcie.get(dev["device"], dev["device"])
            for t in dev["temps"]:
                if t["input"] is None:
                    continue
                is_module = t["label"].lower().startswith("module")
                warn, crit = (th["module_warn"], th["module_crit"]) if is_module else (th["nic_warn"], th["nic_crit"])
                what = f"{label} {t['label']}"
                if t["input"] <= 0:
                    dead_by_card.setdefault(rcn.card_of(dev["device"]) if ":" in dev["device"] else dev["device"], []).append(what)
                else:
                    findings.append(Finding("nic_temps", level_for(t["input"], warn, crit), f"{what}: {t['input']:.0f} C", t["input"]))
        elif dev["name"] == "k10temp":
            for t in dev["temps"]:
                if t["input"] is not None and t["label"].lower() in ("tctl", "tdie", "temp1"):
                    findings.append(Finding("cpu_temp", level_for(t["input"], th["cpu_warn"], th["cpu_crit"]), f"CPU {t['label']}: {t['input']:.0f} C", t["input"]))
                    break
        elif dev["name"] == "nvme":
            for t in dev["temps"]:
                if t["input"] is None or t["label"].lower() != "composite":
                    continue
                warn = t["max"] if t["max"] and t["max"] > 0 else th["nvme_warn"]
                crit = t["crit"] if t["crit"] and t["crit"] > 0 else th["nvme_crit"]
                findings.append(Finding("nvme_temps", level_for(t["input"], warn, crit), f"NVMe at {dev['device']}: {t['input']:.0f} C (max {warn:.0f})", t["input"]))
    for card, sensors in sorted(dead_by_card.items()):
        findings.append(Finding("nic_temps", "warn", f"card {card}: {len(sensors)} sensors read 0 C (firmware not answering): {', '.join(sensors)}", 0))
    if not any(f.check == "nic_temps" for f in findings):
        findings.append(Finding("nic_temps", "warn", "no mlx5 hwmon sensors found"))
    if with_gpu:
        findings += check_gpus(host, th)
    return collapse_ok(findings)


def check_gpus(host: Host, th: dict) -> list[Finding]:
    res = host.run(["nvidia-smi", "--query-gpu=index,name,temperature.gpu", "--format=csv,noheader,nounits"], timeout=20)
    if res.returncode != 0:
        return [Finding("gpu_temps", "warn", f"nvidia-smi failed (rc {res.returncode}): {(res.stderr or res.stdout).strip()[:160]}")]
    findings = []
    for line in res.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3 or not parts[2].isdigit():
            continue
        temp = int(parts[2])
        findings.append(Finding("gpu_temps", level_for(temp, th["gpu_warn"], th["gpu_crit"]), f"GPU {parts[0]} {parts[1]}: {temp} C", temp))
    return findings or [Finding("gpu_temps", "warn", "nvidia-smi returned no GPUs")]


def data_mounts(config: dict) -> list[str]:
    mounts = ["/"]
    for dev in config.get("storage_devices") or []:
        for part in dev.get("partitions") or []:
            mp = part.get("mount_point") or part.get("mountpoint")
            if mp and str(mp).startswith("/mnt") and mp not in mounts:
                mounts.append(mp)
    return mounts


def check_disk_space(host: Host, config: dict, th: dict) -> list[Finding]:
    findings = []
    for mount in data_mounts(config):
        try:
            st = host.statvfs(mount)
        except OSError as exc:
            findings.append(Finding("disk_space", "warn", f"{mount}: {exc.strerror}"))
            continue
        if st.f_blocks == 0:
            continue
        free_pct = 100.0 * st.f_bavail / st.f_blocks
        free_gb = st.f_bavail * st.f_frsize / 1e9
        level = "crit" if free_pct < th["disk_crit_pct"] else "warn" if free_pct < th["disk_warn_pct"] else "ok"
        findings.append(Finding("disk_space", level, f"{mount}: {free_pct:.0f}% free ({free_gb:.0f} GB)", round(free_pct, 1)))
    return collapse_ok(findings)


def check_ptp(host: Host, config: dict, th: dict) -> list[Finding]:
    nics = rcn.mellanox_nics(config)
    if not any(n["role"] == "camera" for n in nics.values()):
        return [Finding("ptp", "info", "no camera NICs configured; PTP not expected")]
    procs = rcn.ptp_processes(host)
    running = {p["name"] for p in procs}
    findings = [Finding("ptp", "warn", f"{name} is not running") for name in rcn.PTP_PROCESSES if name not in running]
    if "phc2sys" in running:
        res = host.run(["journalctl", "_COMM=phc2sys", "-o", "cat", "--no-pager", "-n", "400", "--since", "-5min"], timeout=30)
        # "<port> sys offset <ns> s<state> freq ..."; servo state s2 = locked, s0/s1 = unlocked or stepping.
        samples = re.findall(r"(\S+) sys offset\s+(-?\d+) s(\d)", res.stdout) if res.returncode == 0 else []
        latest_state = {port: state for port, _, state in samples}
        unlocked = sorted(port for port, state in latest_state.items() if state != "2")
        locked = [int(off) for _, off, state in samples if state == "2"]
        if not samples:
            findings.append(Finding("ptp", "warn", "phc2sys running but no offset lines in the last 5 min"))
        elif unlocked:
            findings.append(Finding("ptp", "crit", f"phc2sys not locked on {', '.join(unlocked)} (servo state != s2)"))
        if locked:
            # judged on locked samples only: the step at start-up (s0/s1) is not a fault once the servo has locked
            worst = max(locked, key=abs)
            findings.append(Finding("ptp", level_for(abs(worst), th["ptp_warn_ns"], th["ptp_crit_ns"]),
                                    f"phc2sys worst locked offset in last 5 min: {worst} ns", worst))
    return collapse_ok(findings) or [Finding("ptp", "ok", "ptp4l and phc2sys running")]


def check_time_sync(host: Host, th: dict, server: str | None) -> list[Finding]:
    """Is anything disciplining the system clock, and how far is it from NTP time?"""
    findings = []
    res = host.run(["timedatectl", "show", "-p", "NTP", "-p", "NTPSynchronized"], timeout=10)
    if res.returncode == 0:
        props = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
        if props.get("NTP") != "yes":
            findings.append(Finding("time_sync", "warn", "no NTP service enabled (timedatectl set-ntp true); the clock drifts freely"))
        elif props.get("NTPSynchronized") != "yes":
            findings.append(Finding("time_sync", "warn", "NTP enabled but the clock is not synchronized yet"))
    else:
        findings.append(Finding("time_sync", "warn", f"timedatectl failed: {(res.stderr or res.stdout).strip()[:120]}"))
    if server:
        try:
            offset = host.sntp_offset(server)
        except OSError as exc:
            findings.append(Finding("time_sync", "info", f"NTP query to {server} failed: {type(exc).__name__}"))
        else:
            direction = "behind" if offset > 0 else "ahead of"
            findings.append(Finding("time_sync", level_for(abs(offset), th["time_warn_s"], th["time_crit_s"]),
                                    f"system clock is {abs(offset):.3f} s {direction} {server}", round(offset, 3)))
    return collapse_ok(findings) or [Finding("time_sync", "ok", "NTP synchronized")]


def check_pcie_aer(host: Host, since: str) -> list[Finding]:
    ok, lines = rcn.kernel_journal(host, since)
    if not ok:
        return [Finding("pcie_aer", "warn", f"could not read the kernel log: {' '.join(lines)[:120]}")]
    hits = [l for l in lines if "AER:" in l]
    if not hits:
        return [Finding("pcie_aer", "ok", f"no AER messages since {since}")]
    devices = sorted({m.group(1) for l in hits for m in [re.search(r"(\S+ 0000:[0-9a-f:.]+):", l)] if m})
    return [Finding("pcie_aer", "warn", f"{len(hits)} PCIe AER messages since {since} on {', '.join(devices) or 'unknown'}", len(hits))]


# --- storage health ------------------------------------------------------------------------------
#
# Why (2026-10-16): /mnt/Data1 (nvme1n1) threw 217 "critical medium error" reads during Orange's
# finalize of a 95-minute run, after SMART had already counted 1,495 media errors. The 00:00 and 00:05
# runs said ok: nothing here read SMART or looked for storage errors in the kernel log.

SYS_NVME = Path("/sys/class/nvme")
SMART_SNAPSHOT = Path("/var/lib/pancakebatter-nvme/smart.json")   # written as root by install_nvme_smart_snapshot.sh
DRIVE_ACKS = "drive_acks.json"                                      # in --state-dir, written by --acknowledge-drive
CTRL_RE = re.compile(r"^nvme\d+$")
NS_RE = re.compile(r"^nvme\d+n\d+$")
STORAGE_PATTERNS = [   # (kind, regex with the block device or controller as group 1)
    ("critical medium error", re.compile(r"critical medium error, dev (\w+)")),
    ("nvme I/O error", re.compile(r"\b(nvme\d+n\d+): I/O Cmd\(.*I/O Error")),
    ("I/O error", re.compile(r"\bI/O error, dev (\w+)")),
    ("buffer I/O error", re.compile(r"Buffer I/O error on dev (\w+)")),
    ("nvme timeout", re.compile(r"\bnvme (nvme\d+): I/O \d+ QID \d+ timeout")),
    ("nvme reset", re.compile(r"\bnvme (nvme\d+): (?:resetting controller|controller is down|Removing after probe failure)")),
]
# SMART field names differ between nvme-cli 1.x (Ubuntu 22.04) and 2.x; first match wins.
SMART_FIELDS = {
    "critical_warning": ("critical_warning",),
    "media_errors": ("media_errors",),
    "num_err_log_entries": ("num_err_log_entries",),
    "percent_used": ("percent_used", "percentage_used"),
    "avail_spare": ("avail_spare", "available_spare"),
    "spare_thresh": ("spare_thresh", "available_spare_threshold"),
    "unsafe_shutdowns": ("unsafe_shutdowns",),
    "warning_temp_time": ("warning_temp_time",),
    "critical_comp_time": ("critical_comp_time",),
    "temperature": ("temperature",),
}
CRITICAL_WARNING_BITS = {0: "spare below threshold", 1: "temperature", 2: "reliability degraded",
                         3: "read-only", 4: "volatile backup failed", 5: "persistent memory read-only"}


def controller_of(device: str) -> str:
    """nvme1n1p1 / nvme1n1 / nvme1 -> nvme1; anything else (sda1) is returned unchanged."""
    m = re.match(r"^(nvme\d+)(?:n\d+(?:p\d+)?)?$", device)
    return m.group(1) if m else device


def nvme_controllers(host: Host) -> dict[str, dict]:
    """{nvme1: {model, serial, firmware_rev, namespaces}} from sysfs (readable without root)."""
    out = {}
    for name in host.listdir(SYS_NVME):
        if not CTRL_RE.match(name):
            continue
        base = SYS_NVME / name
        out[name] = {"model": host.read(base / "model") or "", "serial": host.read(base / "serial") or "",
                     "firmware_rev": host.read(base / "firmware_rev") or "",
                     "namespaces": [n for n in host.listdir(base) if NS_RE.match(n)]}
    return out


def drive_key(ctrl: str, controllers: dict) -> str:
    """Serial when known (stable across renumbering), else the controller name."""
    return (controllers.get(ctrl) or {}).get("serial") or ctrl


def drive_label(ctrl: str, controllers: dict) -> str:
    c = controllers.get(ctrl) or {}
    return f"{ctrl} ({c['model']} SN {c['serial']})" if c.get("serial") else ctrl


def load_drive_acks(state_dir: Path | None) -> dict:
    if not state_dir:
        return {}
    try:
        data = json.loads((state_dir / DRIVE_ACKS).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_smart_snapshot(host: Host) -> tuple[dict | None, str | None]:
    raw = host.read(SMART_SNAPSHOT)
    if raw is None:
        return None, None
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        return None, f"{SMART_SNAPSHOT} unparseable: {exc}"
    return (doc, None) if isinstance(doc, dict) and isinstance(doc.get("controllers"), dict) else (None, f"{SMART_SNAPSHOT} has no controllers")


def smart_value(smart: dict, field: str):
    for name in SMART_FIELDS[field]:
        v = smart.get(name)
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.strip().rstrip("%").isdigit():   # some builds print "3%"
            return int(v.strip().rstrip("%"))
    return None


def storage_error_counts(lines: list[str]) -> dict[str, dict[str, int]]:
    """{controller or block device: {kind: count}} from kernel log lines."""
    out: dict[str, dict[str, int]] = {}
    for line in lines:
        for kind, rx in STORAGE_PATTERNS:
            m = rx.search(line)
            if m:
                dev = controller_of(m.group(1))
                out.setdefault(dev, {})
                out[dev][kind] = out[dev].get(kind, 0) + 1
                break
    return out


def check_storage_errors(host: Host, since: str, now: str, previous: dict, acks: dict, memo: dict,
                         controllers: dict | None = None, only: set[str] | None = None) -> list[Finding]:
    """Kernel storage errors since the last run, kept (crit) per drive until --acknowledge-drive.

    memo["storage_errors"] carries {drive key: {"kinds": {...}, "first": t, "last": t}} into the next run's state."""
    controllers = nvme_controllers(host) if controllers is None else controllers
    ok, lines = rcn.kernel_journal(host, since)
    carried = {k: dict(v, kinds=dict(v.get("kinds") or {}))
               for k, v in ((previous.get("storage") or {}).get("storage_errors") or {}).items()}
    if ok:
        for dev, kinds in storage_error_counts(lines).items():
            entry = carried.setdefault(drive_key(dev, controllers), {"device": dev, "kinds": {}, "first": now})
            entry["device"], entry["last"] = dev, now
            for kind, n in kinds.items():
                entry["kinds"][kind] = entry["kinds"].get(kind, 0) + n
    for key in list(carried):   # an acknowledgement after the last error clears the drive
        acked_at = (acks.get(key) or {}).get("acknowledged_at")
        if acked_at and acked_at >= carried[key].get("last", carried[key].get("first", "")):
            del carried[key]
    memo["storage_errors"] = carried
    findings = []
    if not ok:
        findings.append(Finding("storage_errors", "warn", f"could not read the kernel log: {' '.join(lines)[:120]}"))
    for key, entry in sorted(carried.items()):
        ctrl = controller_of(entry.get("device", key))
        if only is not None and ctrl not in only:
            continue
        kinds = ", ".join(f"{n} {k}" for k, n in sorted(entry["kinds"].items()))
        findings.append(Finding("storage_errors", "crit",
                                f"{drive_label(ctrl, controllers)}: {kinds} since {entry.get('first')} "
                                f"(stays crit until ./rig_health_check.py --state-dir DIR --acknowledge-drive {key})",
                                sum(entry["kinds"].values())))
    return findings or [Finding("storage_errors", "ok", f"no kernel storage errors since {since}")]


def check_nvme_firmware(host: Host, config: dict, controllers: dict | None = None) -> list[Finding]:
    """Same-model drives on different firmware, and drives whose firmware differs from the host config."""
    controllers = nvme_controllers(host) if controllers is None else controllers
    findings = []
    by_model: dict[str, dict[str, list[str]]] = {}
    for ctrl, c in sorted(controllers.items()):
        by_model.setdefault(c["model"], {}).setdefault(c["firmware_rev"], []).append(ctrl)
    for model, revs in sorted(by_model.items()):
        if len(revs) > 1:
            detail = "; ".join(f"{rev}: {', '.join(ctrls)}" for rev, ctrls in sorted(revs.items()))
            findings.append(Finding("nvme_firmware", "warn", f"{model} drives on different firmware ({detail})"))
    declared = {str(d.get("serial_number")): d for d in config.get("storage_devices") or [] if d.get("serial_number")}
    for ctrl, c in sorted(controllers.items()):
        d = declared.get(c["serial"])
        if d and d.get("firmware_version") and str(d["firmware_version"]) != c["firmware_rev"]:
            findings.append(Finding("nvme_firmware", "warn", f"{drive_label(ctrl, controllers)}: firmware {c['firmware_rev']}, "
                                                             f"host config says {d['firmware_version']}"))
    if findings:
        return findings
    return [Finding("nvme_firmware", "ok", ", ".join(f"{ctrl} {c['firmware_rev']}" for ctrl, c in sorted(controllers.items())) or "no NVMe controllers")]


def check_nvme_smart(host: Host, th: dict, now_ts: float, previous: dict, acks: dict, memo: dict,
                     only: set[str] | None = None) -> list[Finding]:
    """SMART from the root snapshot: health flags, media errors against the acknowledged baseline, and
    increases since the last run. memo["smart"] carries {drive key: counters} into the next run's state."""
    doc, error = load_smart_snapshot(host)
    if error:
        return [Finding("nvme_smart", "warn", error)]
    if doc is None:
        return [Finding("nvme_smart", "info", f"no SMART snapshot at {SMART_SNAPSHOT} (sudo ./install_nvme_smart_snapshot.sh)")]
    findings = []
    try:
        age = now_ts - datetime.fromisoformat(doc["time"]).timestamp()
    except (KeyError, TypeError, ValueError):
        age = None
    if age is None or age > th["smart_stale_s"]:
        findings.append(Finding("nvme_smart", "warn", f"SMART snapshot is stale (taken {doc.get('time')}); "
                                                      "systemctl status nvme-smart-snapshot.timer"))
    before = (previous.get("storage") or {}).get("smart") or {}
    counters: dict[str, dict] = {}
    controllers = {c: {"model": v.get("model") or "", "serial": v.get("serial") or ""} for c, v in doc["controllers"].items()}
    for ctrl, entry in sorted(doc["controllers"].items()):
        if only is not None and ctrl not in only:
            continue
        label = drive_label(ctrl, controllers)
        if entry.get("error") or not isinstance(entry.get("smart"), dict):
            findings.append(Finding("nvme_smart", "warn", f"{label}: smart-log failed: {entry.get('error')}"))
            continue
        smart = entry["smart"]
        key = drive_key(ctrl, controllers)
        v = {f: smart_value(smart, f) for f in SMART_FIELDS}
        counters[key] = v
        prev = before.get(key) or {}
        rose = lambda f: v[f] is not None and prev.get(f) is not None and v[f] > prev[f]

        cw = v["critical_warning"] or 0
        if cw:
            bits = [name for bit, name in CRITICAL_WARNING_BITS.items() if cw & (1 << bit)]
            findings.append(Finding("nvme_smart", "crit", f"{label}: critical_warning 0x{cw:02x} ({', '.join(bits) or 'unknown bits'})", cw))
        baseline = (acks.get(key) or {}).get("media_errors", 0)
        if v["media_errors"] is not None and v["media_errors"] > baseline:
            since_ack = f"{v['media_errors'] - baseline} new since the acknowledgement of {baseline}" if key in acks else "never acknowledged"
            findings.append(Finding("nvme_smart", "crit",
                                    f"{label}: {v['media_errors']} media errors ({since_ack}; "
                                    f"./rig_health_check.py --state-dir DIR --acknowledge-drive {key} once handled)", v["media_errors"]))
        if v["avail_spare"] is not None and v["spare_thresh"] is not None and v["avail_spare"] <= v["spare_thresh"]:
            findings.append(Finding("nvme_smart", "crit", f"{label}: available spare {v['avail_spare']}% at or below its threshold {v['spare_thresh']}%", v["avail_spare"]))
        if v["percent_used"] is not None and v["percent_used"] >= th["nvme_used_warn_pct"]:
            findings.append(Finding("nvme_smart", "warn", f"{label}: {v['percent_used']}% of rated endurance used", v["percent_used"]))
        for field, what in (("num_err_log_entries", "error-log entries"), ("unsafe_shutdowns", "unsafe shutdowns"),
                            ("warning_temp_time", "minutes above the warning temperature"),
                            ("critical_comp_time", "minutes above the critical temperature")):
            if rose(field):
                findings.append(Finding("nvme_smart", "warn", f"{label}: {what} {prev[field]} -> {v[field]}", v[field] - prev[field]))
    memo["smart"] = {**before, **counters}
    if any(f.level != "ok" for f in findings):
        return findings
    return [Finding("nvme_smart", "ok", f"{len(counters)} drives healthy (snapshot {doc.get('time')})")]


def acknowledge_drive(host: Host, state_dir: Path, ident: str, note: str = "") -> str:
    """Record that a drive's current media errors and kernel errors are known; only newer ones alert again."""
    controllers = nvme_controllers(host)
    match = [c for c, v in controllers.items() if controller_of(ident) == c or ident == v["serial"]]
    if len(match) > 1:
        raise ValueError(f"{ident!r} matches {', '.join(match)}")
    ctrl = match[0] if match else ident
    key = drive_key(ctrl, controllers)
    doc, _ = load_smart_snapshot(host)
    smart = ((doc or {}).get("controllers", {}).get(ctrl) or {}).get("smart") or {}
    acks = load_drive_acks(state_dir)
    acks[key] = {"acknowledged_at": datetime.now().isoformat(timespec="seconds"),
                 "media_errors": smart_value(smart, "media_errors") if smart else (acks.get(key) or {}).get("media_errors", 0),
                 "controller": ctrl, "note": note}
    state_dir.mkdir(parents=True, exist_ok=True)
    tmp = state_dir / (DRIVE_ACKS + ".tmp")
    tmp.write_text(json.dumps(acks, indent=2) + "\n")
    tmp.replace(state_dir / DRIVE_ACKS)
    return f"acknowledged {drive_label(ctrl, controllers)}: media_errors baseline {acks[key]['media_errors']}"


def backing_controller(host: Host, path: str) -> tuple[str | None, str]:
    """(nvme controller, block device) that holds `path`, from st_dev -> /sys/dev/block/MAJ:MIN."""
    major, minor = host.stat_dev(path)
    real = host.realpath(f"/sys/dev/block/{major}:{minor}")
    parts = Path(real).parts
    block = parts[-1] if parts else ""
    ctrl = next((p for p in reversed(parts) if CTRL_RE.match(p)), None)
    return ctrl, block


def preflight(host: Host, config: dict, th: dict, root: str, expected_gb: float | None,
              since: str, previous: dict, acks: dict) -> list[Finding]:
    """Is the drive under the recording root fit to record onto? Read-only: saves no state."""
    try:
        ctrl, block = backing_controller(host, root)
    except OSError as exc:
        return [Finding("preflight", "crit", f"{root}: {exc.strerror or exc}")]
    findings = [Finding("preflight", "info", f"{root} is on {block}" + (f" (controller {ctrl})" if ctrl else " (not an NVMe drive)"))]
    try:
        st = host.statvfs(root)
        free_gb = st.f_bavail * st.f_frsize / 1e9
        free_pct = 100.0 * st.f_bavail / st.f_blocks if st.f_blocks else 0.0
    except OSError as exc:
        return findings + [Finding("preflight", "crit", f"{root}: {exc.strerror or exc}")]
    if expected_gb is not None:
        need = expected_gb * (1 + th["preflight_margin_pct"] / 100)
        level = "crit" if free_gb < need else "ok"
        findings.append(Finding("disk_space", level, f"{root}: {free_gb:.0f} GB free, run needs {expected_gb:.0f} GB "
                                                     f"+ {th['preflight_margin_pct']:.0f}% = {need:.0f} GB", round(free_gb)))
    level = "crit" if free_pct < th["disk_crit_pct"] else "warn" if free_pct < th["disk_warn_pct"] else "ok"
    findings.append(Finding("disk_space", level, f"{root}: {free_pct:.0f}% free", round(free_pct, 1)))
    if ctrl:
        controllers = nvme_controllers(host)
        memo: dict = {}
        findings += check_storage_errors(host, since, datetime.now().isoformat(timespec="seconds"), previous, acks, memo,
                                         controllers, only={ctrl})
        findings += check_nvme_smart(host, th, datetime.now().timestamp(), previous, acks, memo, only={ctrl})
    return findings


def check_host_config(host: Host, checkout: Path) -> list[Finding]:
    """Is the installed copy other programs read (/etc/pancakebatter/host.yml) the same as the checkout file?"""
    installed = hostconfig.INSTALLED_CONFIG
    if not host.exists(installed):
        return [Finding("host_config", "info", f"{installed} not installed (sudo ./install_host_config.sh); consumers cannot resolve a host config")]
    a, b = host.read(installed), host.read(checkout)
    if a is None or b is None:
        return [Finding("host_config", "warn", f"could not read {installed if a is None else checkout}")]
    if a.strip() != b.strip():
        return [Finding("host_config", "warn", f"{installed} differs from {checkout}: re-run sudo ./install_host_config.sh")]
    return [Finding("host_config", "ok", f"{installed} matches the checkout")]


def check_recording_transfer(config: dict) -> list[Finding]:
    """Every installed sealer version still matches its releases.json entry, file by file."""
    root_value = (config.get("paths") or {}).get("recording_transfer_root")
    if not root_value:
        return []
    import recording_transfer_install as rti
    root = Path(root_value)
    versions = rti.installed_versions(root)
    if not versions:
        return [Finding("recording_transfer", "info", f"no sealer versions installed under {root}")]
    try:
        manifest = rti.load_manifest()
    except (OSError, ValueError) as exc:
        return [Finding("recording_transfer", "warn", f"releases.json unreadable: {exc}")]
    problems = [p for v in versions for p in rti.verify_install(root, v, manifest)]
    if problems:
        return [Finding("recording_transfer", "warn", p) for p in problems]
    return [Finding("recording_transfer", "ok", f"{', '.join(versions)} intact under {root}")]


def check_acquisition(host: Host) -> list[Finding]:
    pids = rcn.orange_pids(host)
    return [Finding("acquisition", "info", f"Orange running (pids {pids})" if pids else "Orange not running", pids)]


def collapse_ok(findings: list[Finding]) -> list[Finding]:
    """Keep every non-ok finding; summarise the ok ones per check so the output stays short."""
    out, oks = [], {}
    for f in findings:
        if f.level == "ok":
            oks.setdefault(f.check, []).append(f)
        else:
            out.append(f)
    for check, group in oks.items():
        if any(f.check == check for f in out):
            continue
        hottest = max((f for f in group if isinstance(f.value, (int, float))), key=lambda f: f.value, default=None)
        out.append(Finding(check, "ok", f"{len(group)} ok" + (f"; highest {hottest.message}" if hottest else ""),
                           hottest.value if hottest else None))
    return out


# --- state, alerts ----------------------------------------------------------------------------

def load_state(state_dir: Path | None) -> dict:
    if not state_dir:
        return {}
    try:
        return json.loads((state_dir / "last.json").read_text())
    except (OSError, ValueError):
        return {}


def save_state(state_dir: Path | None, result: dict) -> None:
    if not state_dir:
        return
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "last.json").write_text(json.dumps(result, indent=2))
    history = state_dir / "history.jsonl"
    with history.open("a") as fh:
        fh.write(json.dumps({"time": result["time"], "levels": result["levels"], "worst": result["worst"]}) + "\n")
    try:
        if history.stat().st_size > 8_000_000:
            lines = history.read_text().splitlines()[-20000:]
            history.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def worst_level(findings: list[Finding]) -> str:
    """ok / warn / crit; info never raises the level (and never prints as the overall status)."""
    return max((f.level for f in findings if f.level != "info"), key=lambda l: LEVEL_RANK[l], default="ok")


def levels_by_check(findings: list[Finding]) -> dict[str, str]:
    levels: dict[str, str] = {}
    for f in findings:
        if LEVEL_RANK[f.level] >= LEVEL_RANK.get(levels.get(f.check, "ok"), 0):
            levels[f.check] = f.level
    return levels


def transitions(previous: dict[str, str], current: dict[str, str]) -> dict[str, tuple[str, str]]:
    """check -> (old, new) for checks whose level changed (info and ok are the same thing here)."""
    norm = lambda l: "ok" if l in ("ok", "info", None) else l
    changed = {}
    for check in set(previous) | set(current):
        old, new = norm(previous.get(check)), norm(current.get(check))
        if old != new:
            changed[check] = (old, new)
    return changed


SYSTEM_MSMTPRC = Path("/etc/msmtprc")


def mail_command(host: Host) -> list[str]:
    """msmtp with the system-wide account by default (RIG_HEALTH_MSMTP_CONFIG overrides; the per-user
    ~/.msmtprc is deliberately not relied on: it may be missing, broken, or unreadable under the unit's
    ProtectHome). Falls back to whatever sendmail is."""
    config = os.environ.get("RIG_HEALTH_MSMTP_CONFIG", "").strip()
    if not config and host.exists(SYSTEM_MSMTPRC):
        config = str(SYSTEM_MSMTPRC)
    return ["msmtp", "-C", config, "-t"] if config else ["msmtp", "-t"]


def send_mail(host: Host, to: str, sender: str, subject: str, body: str) -> str:
    message = f"To: {to}\nFrom: {sender}\nSubject: {subject}\n\n{body}\n"
    for cmd in (mail_command(host), ["sendmail", "-t"]):
        proc = host.run(cmd, timeout=30, input=message)
        if proc.returncode == 127:
            continue
        if proc.returncode == 0:
            return f"mailed {to} via {' '.join(cmd[:3])}"
        return f"{cmd[0]} failed rc {proc.returncode}: {proc.stderr.strip()[:200]}"
    return "no msmtp/sendmail available"


# --- main ------------------------------------------------------------------------------------

def run_checks(host: Host, config: dict, th: dict, since: str, with_gpu: bool, with_ptp: bool,
               ntp_server: str | None = NTP_SERVER, config_path: Path | None = None,
               previous: dict | None = None, acks: dict | None = None, memo: dict | None = None) -> list[Finding]:
    """memo, when given, receives the storage counters to save for the next run's deltas."""
    previous, acks, memo = previous or {}, acks or {}, memo if memo is not None else {}
    findings = []
    if config_path is not None:
        findings += check_host_config(host, config_path)
    findings += check_camera_nics(host, config)
    findings += check_mlx5_health(host, config, since)
    findings += check_temperatures(host, config, th, with_gpu)
    findings += check_disk_space(host, config, th)
    if with_ptp:
        findings += check_ptp(host, config, th)
    findings += check_time_sync(host, th, ntp_server)
    findings += check_pcie_aer(host, since)
    controllers = nvme_controllers(host)
    findings += check_storage_errors(host, since, datetime.now().isoformat(timespec="seconds"), previous, acks, memo, controllers)
    findings += check_nvme_smart(host, th, datetime.now().timestamp(), previous, acks, memo)
    findings += check_nvme_firmware(host, config, controllers)
    findings += check_recording_transfer(config)
    findings += check_acquisition(host)
    return findings


def format_findings(result: dict) -> str:
    lines = [f"rig health on {result['host']} at {result['time']}: {result['worst'].upper()}"]
    for f in result["findings"]:
        lines.append(f"  [{f['level']:<4}] {f['check']:<12} {f['message']}")
    for check, (old, new) in sorted(result.get("transitions", {}).items()):
        lines.append(f"  change: {check} {old} -> {new}")
    if result.get("mail"):
        lines.append(f"  {result['mail']}")
    return "\n".join(lines)


LEVEL_STYLE = {"ok": "green", "info": "cyan", "warn": "yellow", "crit": "bold red"}   # rich styles
LEVEL_ANSI = {"ok": "\033[32m", "info": "\033[36m", "warn": "\033[33m", "crit": "\033[1;31m"}
ANSI_BOLD, ANSI_DIM, ANSI_RESET = "\033[1m", "\033[2m", "\033[0m"


def want_color(mode: str, stream) -> bool:
    """auto = colour only on a terminal (never in the journal, a pipe or a mail body) and not under NO_COLOR."""
    if mode in ("always", "never"):
        return mode == "always"
    return bool(getattr(stream, "isatty", lambda: False)()) and not os.environ.get("NO_COLOR")


def load_rich():
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.text import Text
    except ImportError:
        return None
    return Console, Table, Text


def render(result: dict, color: bool, stream=None, rich_mods="auto") -> str:
    """Print the findings; returns which renderer was used ('plain', 'rich' or 'ansi')."""
    stream = stream or sys.stdout
    if not color:
        print(format_findings(result), file=stream)
        return "plain"
    mods = load_rich() if rich_mods == "auto" else rich_mods
    head = f"rig health on {result['host']} at {result['time']}: "
    worst = result["worst"]
    changes = sorted(result.get("transitions", {}).items())
    if mods:
        Console, Table, Text = mods
        console = Console(file=stream, force_terminal=True, highlight=False)
        console.print(Text.assemble(head, (worst.upper(), LEVEL_STYLE[worst])))
        table = Table(box=None, show_header=False, pad_edge=False, padding=(0, 1))
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True, style="bold")
        table.add_column(overflow="fold")
        for f in result["findings"]:
            # Text(), not markup strings: "[ok]" would otherwise be parsed as a style tag
            table.add_row(Text(f" [{f['level']:<4}]", style=LEVEL_STYLE[f["level"]]), f["check"], Text(f["message"]))
        console.print(table)
        for check, (old, new) in changes:
            console.print(Text.assemble("  change: ", (check, "bold"), f" {old} -> ", (new, LEVEL_STYLE.get(new, ""))))
        if result.get("mail"):
            console.print(Text(f"  {result['mail']}", style="dim"))
        return "rich"
    lines = [f"{head}{LEVEL_ANSI[worst]}{worst.upper()}{ANSI_RESET}"]
    for f in result["findings"]:
        lines.append(f"  {LEVEL_ANSI[f['level']]}[{f['level']:<4}]{ANSI_RESET} {ANSI_BOLD}{f['check']:<12}{ANSI_RESET} {f['message']}")
    for check, (old, new) in changes:
        lines.append(f"  change: {ANSI_BOLD}{check}{ANSI_RESET} {old} -> {LEVEL_ANSI.get(new, '')}{new}{ANSI_RESET}")
    if result.get("mail"):
        lines.append(f"  {ANSI_DIM}{result['mail']}{ANSI_RESET}")
    print("\n".join(lines), file=stream)
    return "ansi"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--config", type=Path, help="host config (default hosts/<hostname>/config.yml)")
    parser.add_argument("--state-dir", type=Path, help="directory for last.json / history.jsonl (enables change detection)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                        help="coloured output: auto = only on a terminal (uses rich when installed, plain ANSI otherwise)")
    parser.add_argument("--no-gpu", action="store_true", help="skip nvidia-smi")
    parser.add_argument("--no-ptp", action="store_true", help="skip the ptp4l/phc2sys check")
    parser.add_argument("--ntp-server", default=NTP_SERVER, help=f"server for the clock offset query (default {NTP_SERVER})")
    parser.add_argument("--no-ntp-query", action="store_true", help="only ask timedatectl; send no NTP packet")
    parser.add_argument("--since", help="journal window for mlx5/AER checks (default: last run, else -15min)")
    parser.add_argument("--threshold", action="append", default=[], metavar="NAME=VALUE", help=f"override one of {', '.join(THRESHOLDS)}")
    parser.add_argument("--print-affinity", action="store_true", help="print the non-isolated CPU list for systemd CPUAffinity and exit")
    parser.add_argument("--test-mail", metavar="ADDRESS", help="send one test message to ADDRESS the way alerts are sent, and exit")
    parser.add_argument("--preflight", action="store_true",
                        help="pre-recording gate: check only the drive under --recording-root; saves no state; exit 2 = do not record")
    parser.add_argument("--recording-root", help="--preflight: the directory the recording will be written to")
    parser.add_argument("--expected-gb", type=float, help="--preflight: expected size of the run in GB (95 min is about 333)")
    parser.add_argument("--acknowledge-drive", metavar="SERIAL|nvmeN",
                        help="record the drive's current media errors and kernel storage errors as known (needs --state-dir), and exit")
    parser.add_argument("--note", default="", help="--acknowledge-drive: why (kept in drive_acks.json)")
    args = parser.parse_args(argv)
    if args.preflight and not args.recording_root:
        parser.error("--preflight needs --recording-root")

    config_path = Path(args.config or hostconfig.default_config_path())
    config = yaml.safe_load(config_path.read_text()) or {}
    if args.print_affinity:
        print(format_cpulist(allowed_cpus(config, os.cpu_count() or 1)))
        return 0
    stay_out_of_the_way(config)

    th = dict(THRESHOLDS)
    for item in args.threshold:
        name, _, value = item.partition("=")
        if name not in th or not value:
            parser.error(f"--threshold {item}: unknown name or missing value")
        th[name] = float(value)

    host = Host()
    if args.test_mail:
        sender = os.environ.get("RIG_HEALTH_FROM", "").strip() or f"rig-health@{socket.gethostname()}"
        outcome = send_mail(host, args.test_mail, sender, MAIL_SUBJECT.format(host=socket.gethostname().split(".")[0], summary="test message"),
                            f"Test from rig_health_check.py on {socket.gethostname()} at {datetime.now().isoformat(timespec='seconds')}.\n"
                            f"Mail command: {' '.join(mail_command(host))}")
        print(outcome)
        return 0 if outcome.startswith("mailed") else 2
    if args.acknowledge_drive:
        if not args.state_dir:
            parser.error("--acknowledge-drive needs --state-dir (the timer's is /var/lib/rig-health)")
        try:
            print(acknowledge_drive(host, args.state_dir, args.acknowledge_drive, args.note))
        except ValueError as exc:
            parser.error(str(exc))
        return 0
    previous = load_state(args.state_dir)
    acks = load_drive_acks(args.state_dir)
    since = args.since or previous.get("time") or "-15min"
    memo: dict = {}
    if args.preflight:
        findings = preflight(host, config, th, args.recording_root, args.expected_gb, since, previous, acks)
    else:
        findings = run_checks(host, config, th, since, with_gpu=not args.no_gpu, with_ptp=not args.no_ptp,
                              ntp_server=None if args.no_ntp_query else args.ntp_server, config_path=config_path,
                              previous=previous, acks=acks, memo=memo)
    levels = levels_by_check(findings)
    result = {
        "host": socket.gethostname().split(".")[0],
        "time": datetime.now().isoformat(timespec="seconds"),
        "since": since,
        "worst": worst_level(findings),
        "levels": levels,
        "findings": [asdict(f) for f in findings],
        "transitions": {k: list(v) for k, v in transitions(previous.get("levels", {}), levels).items()},
    }
    if args.preflight:   # a gate, not a run: no mail, no state, so the timer's deltas and journal window are untouched
        result["preflight"] = {"recording_root": args.recording_root, "expected_gb": args.expected_gb}
        del result["transitions"]
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            render(result, want_color(args.color, sys.stdout))
        return LEVEL_RANK[result["worst"]]
    result["storage"] = memo

    recipient = os.environ.get("RIG_HEALTH_EMAIL", "").strip()
    if recipient and args.state_dir and result["transitions"]:
        sender = os.environ.get("RIG_HEALTH_FROM", "").strip() or f"rig-health@{socket.gethostname()}"
        subject = MAIL_SUBJECT.format(host=result["host"], summary=", ".join(f"{c} {o}->{n}" for c, (o, n) in sorted(result["transitions"].items())))
        result["mail"] = send_mail(host, recipient, sender, subject, format_findings(result))

    save_state(args.state_dir, result)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        render(result, want_color(args.color, sys.stdout))
    return LEVEL_RANK[result["worst"]]


if __name__ == "__main__":
    sys.exit(main())
