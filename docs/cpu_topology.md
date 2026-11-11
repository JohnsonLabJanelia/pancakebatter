# pancake0's processor: cores, chiplets, and who runs where

A reference for how pancake0's CPU is laid out, so pinning decisions and faults like the
2026-11-11 chiplet clock fault ([`time_and_clocks.md`](time_and_clocks.md#the-2026-11-11-chiplet-clock-fault))
can be read against the hardware. Companion to [`kernel_tuning.md`](kernel_tuning.md) (which
CPUs are isolated, and why) and [`pcie_topology.md`](pcie_topology.md) (which devices share
each PCIe host bridge).

Each fact is marked **measured** (read on the host, with the date) or **inferred** (reasoned
from measurements or documentation, not observed directly).

## The chip

| Fact | Value | Source |
|---|---|---|
| Model | AMD Ryzen Threadripper PRO 5975WX (Zen 3, "Chagall") | measured, `lscpu`, 2026-11-11 |
| Cores / threads | 32 cores, 2 threads per core = 64 logical CPUs, one socket | measured |
| NUMA | one node, CPUs 0-63 | measured, `/sys/devices/system/node` |
| Caches | L1d 32 KiB and L2 512 KiB per core; L3 4 x 32 MiB | measured, `lscpu` |
| Clock range | 1.8 GHz min, boosts up to the reported max; the TSC runs at a fixed 3593.249 MHz regardless | measured |
| Kernel | 6.5.0-44-generic, HZ=250 (one scheduler tick every 4 ms) | measured |
| BIOS / microcode | BIOS 1201 (2023-04-19), microcode 0xa008205 | measured |

### Chiplets (CCDs) and L3 groups

The 5975WX is not one piece of silicon. It is four **core chiplets** (CCDs), each with 8 cores
and its own 32 MiB L3 cache, plus one **I/O die** that holds the memory controllers, the PCIe
lanes and the links between chiplets. Two cores on the same chiplet share an L3 and talk
cheaply. Two cores on different chiplets talk through the I/O die, which costs more.

| L3 group (chiplet) | Logical CPUs | Source |
|---|---|---|
| 0 | 0-7 and 32-39 | measured, `cache/index3/shared_cpu_list` |
| 1 | 8-15 and 40-47 | measured |
| 2 | 16-23 and 48-55 | measured |
| 3 | 24-31 and 56-63 | measured; the chiplet with the 2026-11-11 clock fault |

### Hyperthreads (SMT)

Each core runs two hardware threads that share its execution units and L1/L2 caches. Linux
numbers them N and N+32: CPU 24 and CPU 56 are the same core (measured,
`topology/core_cpus_list` = `24,56`). Two busy threads on one core slow each other down.
That's why each isolated CPU's sibling is isolated too and kept idle (for example 6 and 38).

## Who runs where

| CPUs | Chiplet | Used by | Source |
|---|---|---|---|
| 1 (sibling 33 idle) | 0 | Citrus stimulus render loop | `kernel_tuning.core_roles`, confirmed by citrus-agent 2026-11-11 |
| 2 (sibling 34 idle) | 0 | Citrus arena update loop | same |
| 6, 8, 10, 12 (siblings 38, 40, 42, 44 idle) | 0 and 1 | Orange YOLO threads, one per camera | same, and Orange 2026-11-11 |
| 7 | 0 | phc2sys | Orange and citrus-agent 2026-11-11 |
| 19 | 2 | ptp4l | same |
| everything else | all | everything else floats, including Citrus's arena workers and Orange's acquisition, recorder push and log-writer threads | same |

The isolated CPUs (`isolcpus`, `nohz_full`, `rcu_nocbs` =
1,2,6,8,10,12,33,34,38,40,42,44) are removed from the scheduler's default placement, have
the periodic tick stopped when one task runs, and have RCU callbacks moved elsewhere. A thread
only lands there if it pins itself (see [`kernel_tuning.md`](kernel_tuning.md)). They all sit
on chiplets 0 and 1. Chiplet 3 has no isolated CPU, so any floating thread can land on it.
That is how the 2026-11-11 fault reached Citrus's arena workers and Orange's acquisition
threads.

## Idle states

When a CPU has nothing to run, the kernel puts it in an idle state. Deeper states save more
power but take longer to wake from.

| State | How it's entered | Exit latency | Source |
|---|---|---|---|
| POLL | spin in a loop | 0 µs | measured, `cpuidle/state*`, all enabled |
| C1 | `MWAIT` | 1 µs | measured |
| C2 | read of ACPI I/O port 0x814 | 18 µs | measured |

In C2 the hardware may power down parts of a core or a whole chiplet and restore their state
on wake. **Inferred, unproven:** the restore of a chiplet's time-stamp counter is one
suspected mechanism for the 2026-11-11 fault.

## Reading these facts yourself

```bash
lscpu                                                         # model, counts, caches
cat /sys/devices/system/cpu/cpu24/cache/index3/shared_cpu_list   # which CPUs share cpu24's L3
cat /sys/devices/system/cpu/cpu24/topology/core_cpus_list        # cpu24's hyperthread sibling
cat /sys/devices/system/cpu/isolated                              # isolated CPUs
grep . /sys/devices/system/cpu/cpu0/cpuidle/state*/{name,latency} # idle states
cat /sys/devices/system/clocksource/clocksource0/current_clocksource
cat /proc/cmdline
```
