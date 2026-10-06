# Kernel tuning for the Orange rig (pancake0)

`hosts/pancake0/config.yml` has a `kernel_tuning` section and `check_kernel_tuning.py`
compares the live host against it (`--record` prints a fresh block). Run it
before a soak or from the GUI launcher so an artifact records whether the host
matched its provisioning. Settings and why they matter, from the 2026-09 soaks:

| Setting | Where it lives | Why |
| --- | --- | --- |
| `isolcpus`, `nohz_full`, `rcu_nocbs` = 1,2,6,8,10,12,33,34,38,40,42,44 | kernel command line (GRUB) | Orange's YOLO threads (6, 8, 10, 12) and Citrus's stimulus render and arena update threads (1, 2) run there, each with its hyperthread sibling (38, 40, 42, 44, 33, 34) kept idle, without scheduler or timer noise. The per-CPU record is `kernel_tuning.core_roles`, below. |
| `tsc=reliable` | kernel command line | The TSC was marked unstable on 2026-09-11 and the host fell back to acpi_pm, inflating every host-side timing for a week. |
| `iommu=pt` | kernel command line | Pass-through IOMMU for the GPUDirect/NIC DMA path (applied 2026-09-19, no measurable effect but safe). |
| `vm.dirty_background_bytes=64 MB`, `vm.dirty_bytes=512 MB` | `/etc/sysctl.d/90-orange-writeback.conf` | Recorders write ~600 MB/s of MP4 into the page cache; the default ratio thresholds let the kernel flush ~3 GB bursts every 31 s over the host bridge that A16 card A shares with the NVMe drives, stalling the pipeline (2026-09-21). |
| `vm.compaction_proactiveness=0` | same file | The proactive compaction daemon migrated 42.6 M pages since boot; each migration flushes TLBs on the cores running Orange, i.e. the isolated cores (15 of 18 interrupt-burst seconds in a 2026-09-23 gate). THP is `madvise` and nothing requests huge pages, so compaction had no consumer. |
| transparent hugepage `enabled=madvise`, `defrag=madvise` | `/sys/kernel/mm/transparent_hugepage` (distribution default) | Keeps direct compaction out of allocations. |
| camera ports MTU 9000 | NetworkManager (`configure_interfaces.sh`) | Jumbo frames: a 20 MB camera frame is ~2,300 packets instead of ~14,000. |
| camera ports' mlx5 completion IRQ CPU set | driver / irqbalance, recorded per port | `check_kernel_tuning.py` enforces the invariant, not the list: no completion interrupt may be allowed on an isolated core. The exact set shifts by a core or two on every driver probe (seen after the 2026-10-06 cold boot), so a difference from the recorded list is printed as a note on a PASS line. On this host the set is every core except the isolated ones. Camera frames do not arrive by interrupt: Rivermax/GPUDirect polls and DMAs straight into GPU memory, so the isolated cores see only Orange's polling threads plus kernel IPIs. |

Rules that follow (all measured, see the Orange journal, 2026-09-22/23):

- No builds by any agent on this host while a soak or gate runs. A `-j4` C++
  compile produced 30-90k inter-processor interrupts per second on the
  isolated cores, NIC discards on card A and dropped frames. Priority and job
  count do not help; interrupts do not respect `nice`.
- After a reboot, run `check_kernel_tuning.py` before trusting any latency
  number; the sysctl file makes the three sysctls persistent, GRUB holds the
  command line.
- Every GUI artifact carries `host_monitor_*` files (heartbeat gaps,
  allocation stalls, isolated-core interrupt bursts with sources, busy
  processes); `validate_gui_ptp_recording.py --require-host-monitor` reads
  them.

Install the sysctl file with:

```bash
sudo cp configs/sysctl/90-orange-writeback.conf /etc/sysctl.d/ && sudo sysctl --system
```

## What each isolated CPU is for (`kernel_tuning.core_roles`)

The isolated set is recorded in `hosts/<hostname>/config.yml` twice over: the
list (`isolated_cores`, plus the `isolcpus`/`nohz_full`/`rcu_nocbs` command-line
values) and, since 2026-10-06, a `core_roles` map that says what each of those
CPUs is for. On pancake0:

| CPU | Role | Evidence |
|---|---|---|
| 6, 8, 10, 12 | YOLO inference thread for camera 2010093, 2010094, 2010095, 2010096 | `yolo_affinity_effective_cpus` in the `Cam*_yolo_perf.csv` of the 2026-09-24 and 2026-10-01 runs (set through `ORANGE_YOLO_AFFINITY_CAM_<serial>`) |
| 38, 40, 42, 44 | hyperthread siblings of 6, 8, 10, 12, kept idle so each YOLO thread has a whole physical core | `/sys/devices/system/cpu/cpuN/topology/thread_siblings_list` |
| 33, 34 | hyperthread siblings of 1, 2, kept idle so the Citrus render and arena update threads have whole physical cores (added 2026-10-06) | same |
| 1 | Citrus stimulus render thread (`StimulusDisplayManager::Run`: GL context, CUDA composition, PBO upload, swap); a short-lived `PrepareStart` thread inherits the CPU at experiment start | citrus @ 48c9158: `src/core/threading_config.h` (`render_core = 1`), `citrus_runtime.threading.render_core` in `citrus/system_config.yml`, env `CITRUS_RENDER_CORE`; pinned at `src/main.cpp` after thread creation |
| 2 | Citrus arena update loop (`ArenaUpdateManager::UpdateLoop`) | same files: `arena_update_core = 2`, env `CITRUS_ARENA_UPDATE_CORE`; pinned in `src/core/ArenaUpdateManager.cpp` |

Entry format (keys are CPU numbers as strings; validated by
`schemas/system_config.v1.schema.json`):

```yaml
kernel_tuning:
  core_roles:
    "6":  {role: yolo, consumer: orange, camera: "2010093"}
    "38": {role: smt_sibling, sibling_of: 6, note: "..."}
    "1":  {role: unassigned, note: "..."}
```

Roles: `acquisition`, `yolo`, `stimulus_render`, `arena_update`, `smt_sibling`
(needs `sibling_of`), `housekeeping`, `other`, `unassigned`; optional `consumer`,
`camera`, `thread`, `note`. `check_kernel_tuning.py` enforces:

- every isolated CPU has an entry and every entry is an isolated CPU (FAIL
  otherwise), so the map cannot drift from the kernel command line;
- an `smt_sibling` really is a hyperthread of the CPU it names, and that CPU
  has a working role (FAIL otherwise);
- `unassigned` CPUs are reported (WARN): isolation with no consumer costs two
  cores of general capacity for nothing;
- a physical core with only one hyperthread isolated is reported (WARN), which is how the 33/34 gap was found.

`check_kernel_tuning.py --record` emits a `core_roles` skeleton with every
isolated CPU `unassigned`, to be filled in by hand.

### 2026-10-06: CPUs 33 and 34 added so Citrus has whole physical cores

Until this change CPUs 1 and 2 (Citrus render and arena update) were isolated
but their hyperthread siblings 33 and 34 were not, an oversight from when the
isolation was first set up: ordinary processes could be scheduled on 33/34 and
share a physical core, its caches and its execution resources with the
stimulus render loop. Citrus's own `src/docs/CPU_AFFINITY_AND_ISOLATION.md`
described it as "logical-CPU isolation ... not complete physical-core
isolation". The isolated set is now `1,2,6,8,10,12,33,34,38,40,42,44`, six whole physical cores:
1/33 and 2/34 for Citrus, 6/38, 8/40, 10/42, 12/44 for Orange's YOLO threads.

Applying a change to the isolated set (between recordings; the host config is
edited first, so `check_kernel_tuning.py` fails on the command line until the
reboot, which is the point of the check):

```bash
sudo cp /etc/default/grub /etc/default/grub.bak.$(date +%Y%m%d)
sudo sed -i 's/1,2,6,8,10,12,38,40,42,44/1,2,6,8,10,12,33,34,38,40,42,44/g' /etc/default/grub        # isolcpus, nohz_full and rcu_nocbs
grep -o 'isolcpus=[^ ]*\|nohz_full=[^ ]*\|rcu_nocbs=[^ "]*' /etc/default/grub    # three identical lists
sudo update-grub
sudo ./install_ptp_units.sh            # both units embed the non-isolated CPU list
sudo ./install_rig_health_timer.sh
sudo reboot                            # a warm reboot is enough for this
# after boot:
cat /sys/devices/system/cpu/isolated   # 1-2,6,8,10,12,33-34,38,40,42,44
./check_kernel_tuning.py               # PASS, no warnings
./rig_health_check.py
```

The two installers are part of the change, not housekeeping: `isolcpus` only
keeps the scheduler from *choosing* those CPUs by default, and a service whose
`CPUAffinity` still lists 33 and 34 may be placed there.

Not on isolated CPUs by design: Citrus's arena worker pool (CPU 24 upward, 4
workers) and its Shaman IPC readers (CPU 20 upward). Orange's acquisition
threads are not pinned at all (no affinity call in its code; the NIC writes
frames into the GPU ring without them, so a late wake costs latency, not
frames), whatever older notes say.

Orange's launch scripts still carry their own `ORANGE_YOLO_AFFINITY_CAM_*`
values (and some test scripts use other cores, including non-isolated ones).
Citrus reads its two cores from `citrus_runtime.threading` in its own
`system_config.yml`. The host config is now the record of intent for both;
having the launchers read the pinning from `core_roles` would make it the
single source.
