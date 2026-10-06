# Kernel tuning for the Orange rig (pancake0)

`hosts/pancake0/config.yml` has a `kernel_tuning` section and `check_kernel_tuning.py`
compares the live host against it (`--record` prints a fresh block). Run it
before a soak or from the GUI launcher so an artifact records whether the host
matched its provisioning. Settings and why they matter, from the 2026-09 soaks:

| Setting | Where it lives | Why |
| --- | --- | --- |
| `isolcpus`, `nohz_full`, `rcu_nocbs` = 1,2,6,8,10,12,38,40,42,44 | kernel command line (GRUB) | Orange's acquisition and YOLO threads run there without scheduler or timer noise. |
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
