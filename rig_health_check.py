#!/usr/bin/env python3
"""Read-only rig health check, meant to run every few minutes from a systemd timer.

  ./rig_health_check.py                  print findings; exit 0 ok, 1 warnings, 2 critical
  ./rig_health_check.py --json           one JSON document instead
  ./rig_health_check.py --state-dir DIR  remember the last result there; level changes are what get mailed
  ./rig_health_check.py --print-affinity the non-isolated CPU list (install_rig_health_timer.sh uses it)

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
  pcie_aer      new PCIe AER messages since the last run (warn)
  acquisition   whether Orange is recording (info only)

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
import subprocess
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
}
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

    def cpu_count(self) -> int:
        return os.cpu_count() or 1


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
        res = host.run(["journalctl", "_COMM=phc2sys", "-o", "cat", "--no-pager", "-n", "12", "--since", "-5min"], timeout=30)
        offsets = [int(m) for m in re.findall(r"sys offset\s+(-?\d+)", res.stdout)] if res.returncode == 0 else []
        if offsets:
            worst = max(offsets, key=abs)
            findings.append(Finding("ptp", level_for(abs(worst), th["ptp_warn_ns"], th["ptp_crit_ns"]),
                                    f"phc2sys worst offset in last 5 min: {worst} ns", worst))
        else:
            findings.append(Finding("ptp", "warn", "phc2sys running but no offset lines in the last 5 min"))
    return collapse_ok(findings) or [Finding("ptp", "ok", "ptp4l and phc2sys running")]


def check_pcie_aer(host: Host, since: str) -> list[Finding]:
    res = host.run(["journalctl", "-k", "-o", "cat", "--no-pager", "--since", since], timeout=60)
    if res.returncode != 0:
        return [Finding("pcie_aer", "warn", f"journalctl failed: {res.stderr.strip()[:120]}")]
    hits = [l for l in res.stdout.splitlines() if "AER:" in l]
    if not hits:
        return [Finding("pcie_aer", "ok", f"no AER messages since {since}")]
    devices = sorted({m.group(1) for l in hits for m in [re.search(r"(\S+ 0000:[0-9a-f:.]+):", l)] if m})
    return [Finding("pcie_aer", "warn", f"{len(hits)} PCIe AER messages since {since} on {', '.join(devices) or 'unknown'}", len(hits))]


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
    return max((f.level for f in findings), key=lambda l: LEVEL_RANK[l], default="ok")


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


def send_mail(host: Host, to: str, sender: str, subject: str, body: str) -> str:
    for cmd in (["msmtp", "-t"], ["sendmail", "-t"]):
        try:
            proc = subprocess.run(cmd, input=f"To: {to}\nFrom: {sender}\nSubject: {subject}\n\n{body}\n",
                                  capture_output=True, text=True, timeout=30)
        except FileNotFoundError:
            continue
        if proc.returncode == 0:
            return f"mailed {to} via {cmd[0]}"
        return f"{cmd[0]} failed rc {proc.returncode}: {proc.stderr.strip()[:200]}"
    return "no msmtp/sendmail available"


# --- main ------------------------------------------------------------------------------------

def run_checks(host: Host, config: dict, th: dict, since: str, with_gpu: bool, with_ptp: bool) -> list[Finding]:
    findings = []
    findings += check_camera_nics(host, config)
    findings += check_mlx5_health(host, config, since)
    findings += check_temperatures(host, config, th, with_gpu)
    findings += check_disk_space(host, config, th)
    if with_ptp:
        findings += check_ptp(host, config, th)
    findings += check_pcie_aer(host, since)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    parser.add_argument("--config", type=Path, help="host config (default hosts/<hostname>/config.yml)")
    parser.add_argument("--state-dir", type=Path, help="directory for last.json / history.jsonl (enables change detection)")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--no-gpu", action="store_true", help="skip nvidia-smi")
    parser.add_argument("--no-ptp", action="store_true", help="skip the ptp4l/phc2sys check")
    parser.add_argument("--since", help="journal window for mlx5/AER checks (default: last run, else -15min)")
    parser.add_argument("--threshold", action="append", default=[], metavar="NAME=VALUE", help=f"override one of {', '.join(THRESHOLDS)}")
    parser.add_argument("--print-affinity", action="store_true", help="print the non-isolated CPU list for systemd CPUAffinity and exit")
    args = parser.parse_args(argv)

    config = yaml.safe_load(Path(args.config or hostconfig.default_config_path()).read_text()) or {}
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
    previous = load_state(args.state_dir)
    since = args.since or previous.get("time") or "-15min"
    findings = run_checks(host, config, th, since, with_gpu=not args.no_gpu, with_ptp=not args.no_ptp)
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

    recipient = os.environ.get("RIG_HEALTH_EMAIL", "").strip()
    if recipient and args.state_dir and result["transitions"]:
        sender = os.environ.get("RIG_HEALTH_FROM", "").strip() or f"rig-health@{socket.gethostname()}"
        subject = MAIL_SUBJECT.format(host=result["host"], summary=", ".join(f"{c} {o}->{n}" for c, (o, n) in sorted(result["transitions"].items())))
        result["mail"] = send_mail(host, recipient, sender, subject, format_findings(result))

    save_state(args.state_dir, result)
    print(json.dumps(result, indent=2) if args.json else format_findings(result))
    return LEVEL_RANK[result["worst"]]


if __name__ == "__main__":
    sys.exit(main())
