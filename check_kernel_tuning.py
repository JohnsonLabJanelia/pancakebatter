#!/usr/bin/env python3
"""Check (or record) the kernel-side tuning the Orange rig depends on.

The provisioning record pancake0_config.yml gains a `kernel_tuning` section:
kernel command-line isolation (isolcpus/nohz_full/rcu_nocbs), tsc and iommu
options, the sysctl values from configs/sysctl/90-orange-writeback.conf,
transparent-hugepage modes, the MTU of every camera port, and the CPU set
each camera port's mlx5 completion interrupts are allowed on, and
`core_roles`: what each isolated CPU is for (every isolated CPU must have an
entry, every entry must be isolated, an smt_sibling must really be the
hyperthread of the CPU it names, and a physical core with one hyperthread left
un-isolated is flagged). The check on
that set is the invariant, not the exact list: no completion interrupt may be
allowed on an isolated core (the driver spreads the vectors slightly
differently on every probe, so the recorded list is only compared for
information). On pancake0
that set is every core EXCEPT the isolated ones: the camera frames arrive
through Rivermax/GPUDirect polling, not interrupts, and the acquisition
threads poll on the isolated cores; what still reaches those cores is
kernel inter-processor interrupts (TLB flushes, function calls), which is
what the sysctl and no-builds rules limit.

  check_kernel_tuning.py            compare the live host against the config; exit 1 on drift
  check_kernel_tuning.py --record   print a kernel_tuning block built from the live host

Why: interrupt storms on the isolated cores (another agent's build, then the
proactive compaction daemon) cost camera frames in 2026-09 soaks; the
settings that prevent them live in GRUB, /etc/sysctl.d and the NIC driver,
none of which this repo recorded before. Run it before a soak, or from the
GUI launcher, so an artifact knows whether the host matched its provisioning.
"""
import argparse
import os
import re
import sys
from pathlib import Path

import yaml

import hostconfig

SCRIPT_DIR = Path(__file__).resolve().parent
CMDLINE_KEYS = ("isolcpus", "nohz_full", "rcu_nocbs", "tsc", "iommu")
THP_FILES = {
    "enabled": "/sys/kernel/mm/transparent_hugepage/enabled",
    "defrag": "/sys/kernel/mm/transparent_hugepage/defrag",
    "khugepaged_defrag": "/sys/kernel/mm/transparent_hugepage/khugepaged/defrag",
}


def read(path):
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def cmdline_options():
    out = {}
    for tok in (read("/proc/cmdline") or "").split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            if k in CMDLINE_KEYS:
                out[k] = v
    return out


def thp_mode(text):
    m = re.search(r"\[(\w+(?:\+\w+)?)\]", text or "")
    return m.group(1) if m else text


def sysctl_expectations(conf_path):
    out = {}
    for line in Path(conf_path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        k, v = [x.strip() for x in line.split("=", 1)]
        out[k] = v
    return out


def sysctl_live(key):
    return read("/proc/sys/" + key.replace(".", "/"))


def camera_ports(config):
    ports = []
    for nic in config.get("nics", []) or []:
        for port in nic.get("ports", []) or []:
            name = port.get("interface") or port.get("name")
            if name and Path(f"/sys/class/net/{name}").exists():
                ports.append(name)
    if not ports:  # fall back to every mlx interface that is up with an address
        for name in sorted(os.listdir("/sys/class/net")):
            if name.startswith("mlnx") and read(f"/sys/class/net/{name}/operstate") == "up":
                ports.append(name)
    return ports


def port_irq_cpus(port):
    """CPUs the port's mlx5 completion interrupts are pinned to, as a sorted list."""
    dev = Path(f"/sys/class/net/{port}/device")
    if not dev.exists():
        return None
    pci = os.path.basename(os.readlink(dev))
    cpus = set()
    for line in open("/proc/interrupts"):
        if f"@pci:{pci}" not in line or "mlx5_comp" not in line:
            continue
        irq = line.split(":")[0].strip()
        aff = read(f"/proc/irq/{irq}/smp_affinity_list")
        if aff is None:
            continue
        for part in aff.split(","):
            if "-" in part:
                a, b = part.split("-")
                cpus.update(range(int(a), int(b) + 1))
            elif part:
                cpus.add(int(part))
    return sorted(cpus)


def parse_cpulist(text):
    """'1,2,6-8' -> {1, 2, 6, 7, 8}."""
    cpus = set()
    for part in str(text or "").replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        cpus.update(range(int(lo), int(hi or lo) + 1))
    return cpus


def irq_verdict(port, live_cpus, recorded_cpus, isolated):
    """(level, message) for one camera port's mlx5 completion-interrupt CPU set.

    The invariant is that none of those interrupts may land on an isolated core. The exact set is not
    one: the driver and irqbalance spread the vectors a little differently on every probe (after the
    2026-10-06 cold boot three ports differed from the recorded lists by one or two cores while still
    avoiding every isolated core), so a difference from the recorded list is reported, not failed.
    """
    if live_cpus is None:
        return "warn", f"{port} mlx5 irq affinity unreadable"
    if not live_cpus:
        return "warn", f"{port} has no mlx5_comp interrupts in /proc/interrupts"
    on_isolated = sorted(set(live_cpus) & set(isolated))
    if on_isolated:
        return "fail", f"{port} mlx5 irqs allowed on isolated cores {on_isolated}"
    note = ""
    recorded = set(recorded_cpus or [])
    if recorded and recorded != set(live_cpus):
        added, removed = sorted(set(live_cpus) - recorded), sorted(recorded - set(live_cpus))
        note = f"; differs from the recorded set (+{added} -{removed}), which is normal after a re-probe"
    return "ok", f"{port} mlx5 irqs avoid the isolated cores ({len(live_cpus)} cpus allowed{note})"


CORE_ROLES = ("acquisition", "yolo", "smt_sibling", "housekeeping", "other", "unassigned")


def thread_siblings(cpu):
    """Other hyperthreads of the same physical core, from sysfs."""
    text = read(f"/sys/devices/system/cpu/cpu{cpu}/topology/thread_siblings_list")
    return sorted(parse_cpulist(text) - {cpu}) if text else []


def core_role_findings(isolated, core_roles, siblings):
    """[(level, message)] for kernel_tuning.core_roles against the isolated set and the CPU topology.

    isolated: set of isolated CPUs. core_roles: the config map (string CPU -> entry) or None.
    siblings: {cpu: [other hyperthreads of its physical core]}.
    """
    out = []
    if not core_roles:
        return [("warn", "no kernel_tuning.core_roles recorded: nothing says what each isolated CPU is for")]
    roles = {}
    for key, entry in core_roles.items():
        try:
            cpu = int(key)
        except (TypeError, ValueError):
            out.append(("fail", f"core_roles key {key!r} is not a CPU number"))
            continue
        roles[cpu] = entry or {}
    for cpu in sorted(isolated - set(roles)):
        out.append(("fail", f"cpu {cpu} is isolated but has no core_roles entry"))
    for cpu in sorted(set(roles) - isolated):
        out.append(("fail", f"core_roles lists cpu {cpu} ({roles[cpu].get('role')}), which is not isolated"))
    for cpu in sorted(set(roles) & isolated):
        entry = roles[cpu]
        role = entry.get("role")
        if role not in CORE_ROLES:
            out.append(("fail", f"cpu {cpu} has unknown role {role!r} (one of {', '.join(CORE_ROLES)})"))
        elif role == "unassigned":
            out.append(("warn", f"cpu {cpu} is isolated but unassigned: {entry.get('note') or 'no consumer recorded'}"))
        elif role == "smt_sibling":
            owner = entry.get("sibling_of")
            if owner not in siblings.get(cpu, []):
                out.append(("fail", f"cpu {cpu} says sibling_of {owner}, but its hyperthread siblings are {siblings.get(cpu, [])}"))
            elif roles.get(owner, {}).get("role") in (None, "smt_sibling", "unassigned"):
                out.append(("fail", f"cpu {cpu} is the sibling of cpu {owner}, which has no working role recorded"))
            else:
                out.append(("ok", f"cpu {cpu}: idle sibling of cpu {owner} ({roles[owner]['role']})"))
        else:
            who = " ".join(str(x) for x in (entry.get("consumer"), entry.get("camera") and f"camera {entry['camera']}") if x)
            out.append(("ok", f"cpu {cpu}: {role}{' for ' + who if who else ''}"))
    # A physical core is only quiet if every one of its hyperthreads is isolated.
    for cpu in sorted(isolated):
        open_siblings = [s for s in siblings.get(cpu, []) if s not in isolated]
        if open_siblings:
            out.append(("warn", f"cpu {cpu} is isolated but its hyperthread sibling(s) {open_siblings} are not: general work "
                                f"scheduled there shares the physical core (add them to isolcpus/nohz_full/rcu_nocbs)"))
    return out


def live_state(config):
    conf = SCRIPT_DIR / "configs" / "sysctl" / "90-orange-writeback.conf"
    state = {
        "cmdline": cmdline_options(),
        "sysctl": {k: sysctl_live(k) for k in sysctl_expectations(conf)},
        "sysctl_file": "/etc/sysctl.d/90-orange-writeback.conf",
        "sysctl_file_present": Path("/etc/sysctl.d/90-orange-writeback.conf").is_file(),
        "transparent_hugepage": {k: thp_mode(read(p)) for k, p in THP_FILES.items()},
        "camera_ports": {},
    }
    for port in camera_ports(config):
        state["camera_ports"][port] = {
            "mtu": int(read(f"/sys/class/net/{port}/mtu") or 0),
            "irq_cpus": port_irq_cpus(port),
        }
    return state


def record_block(state):
    isolated = state["cmdline"].get("isolcpus", "")
    lines = [
        "",
        "# Kernel-side tuning the Orange rig depends on (recorded by check_kernel_tuning.py --record).",
        "# Drift here shows up as camera frame loss under load; see docs/kernel_tuning.md.",
        "kernel_tuning:",
        "  cmdline:",
    ]
    for k in CMDLINE_KEYS:
        if k in state["cmdline"]:
            lines.append(f'    {k}: "{state["cmdline"][k]}"')
    lines += [
        f'  isolated_cores: "{isolated}"',
        "  core_roles:  # fill in: acquisition | yolo | smt_sibling (with sibling_of) | housekeeping | other | unassigned",
        *[f'    "{cpu}":\n      role: unassigned' for cpu in sorted(parse_cpulist(isolated))],
        f'  sysctl_file: "{state["sysctl_file"]}"  # copy of configs/sysctl/90-orange-writeback.conf',
        "  sysctl:",
    ]
    for k, v in state["sysctl"].items():
        lines.append(f"    {k}: {v}")
    lines.append("  transparent_hugepage:")
    for k, v in state["transparent_hugepage"].items():
        lines.append(f"    {k}: {v}")
    lines.append("  camera_ports:")
    for port, info in state["camera_ports"].items():
        lines.append(f"    {port}:")
        lines.append(f"      mtu: {info['mtu']}")
        lines.append(f"      mlx5_irq_cpus: [{', '.join(str(c) for c in info['irq_cpus'] or [])}]")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(hostconfig.default_config_path()))
    ap.add_argument("--record", action="store_true", help="print a kernel_tuning block from the live host")
    a = ap.parse_args()
    config = yaml.safe_load(open(a.config)) or {}
    state = live_state(config)
    if a.record:
        print(record_block(state), end="")
        return 0
    expected = config.get("kernel_tuning")
    if not expected:
        print(f"[FAIL] {a.config} has no kernel_tuning section (run with --record and paste the block)")
        return 1
    errors = 0
    warnings = 0

    def ok(msg):
        print(f"[PASS] {msg}")

    def fail(msg):
        nonlocal errors
        errors += 1
        print(f"[FAIL] {msg}")

    def warn(msg):
        nonlocal warnings
        warnings += 1
        print(f"[WARN] {msg}")

    for k, v in (expected.get("cmdline") or {}).items():
        live = state["cmdline"].get(k)
        (ok if str(live) == str(v) else fail)(f"cmdline {k}={live} (expected {v})")
    for k, v in (expected.get("sysctl") or {}).items():
        live = state["sysctl"].get(k)
        (ok if str(live) == str(v) else fail)(f"sysctl {k}={live} (expected {v})")
    (ok if state["sysctl_file_present"] else fail)(
        f"{state['sysctl_file']} {'present' if state['sysctl_file_present'] else 'MISSING: settings are runtime-only'}")
    for k, v in (expected.get("transparent_hugepage") or {}).items():
        live = state["transparent_hugepage"].get(k)
        (ok if str(live) == str(v) else fail)(f"transparent_hugepage {k}={live} (expected {v})")
    isolated = parse_cpulist(expected.get("isolated_cores") or (expected.get("cmdline") or {}).get("isolcpus")
                             or state["cmdline"].get("isolcpus"))
    for level, msg in core_role_findings(isolated, expected.get("core_roles"), {c: thread_siblings(c) for c in isolated}):
        {"ok": ok, "warn": warn, "fail": fail}[level](msg)
    for port, exp in (expected.get("camera_ports") or {}).items():
        live = state["camera_ports"].get(port)
        if not live:
            fail(f"camera port {port} not present")
            continue
        (ok if live["mtu"] == exp.get("mtu") else fail)(f"{port} mtu={live['mtu']} (expected {exp.get('mtu')})")
        level, msg = irq_verdict(port, live["irq_cpus"], exp.get("mlx5_irq_cpus"), isolated)
        {"ok": ok, "warn": warn, "fail": fail}[level](msg)
    print(f"\nkernel tuning: {'PASS' if errors == 0 else 'FAIL'} ({errors} errors, {warnings} warnings)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
