# Time on the rig host: the crystal, ppm, and which clock to read

Companion to [`ptp_services.md`](ptp_services.md) (how the camera clocks follow
the host) and [`kernel_tuning.md`](kernel_tuning.md) (`tsc=reliable`). Written
after finding the host clock 8 minutes behind on 2026-10-05.

## Where the host's time physically comes from

1. **A quartz crystal on the motherboard.** A sliver of quartz cut to a precise
   size, with electrodes on it. Quartz is piezoelectric: a voltage bends it and
   bending it produces a voltage, so a small circuit can keep it ringing at its
   mechanical resonance like a tuning fork, typically 25 MHz on a PC board.
   The frequency is set by the physical dimensions of the cut, which is why it
   is stable to parts per million rather than percent, and also why it is never
   exactly right: manufacturing tolerance is tens of ppm, quartz changes
   frequency with temperature (an "AT-cut" crystal runs slow when it is hotter
   or colder than its ~25 C turnover point, and a running workstation is well
   above that), and it ages slowly.
2. **A clock generator** multiplies that reference with phase-locked loops into
   the hundreds of MHz the CPU, PCIe and memory need.
3. **The TSC** (Time Stamp Counter) is a 64-bit counter in the CPU that
   increments at a fixed rate derived from that reference, a few GHz. It is
   the kernel's clocksource here (`tsc=reliable` keeps it that way, see
   `kernel_tuning.md`).
4. **The kernel turns counter ticks into nanoseconds** by multiplying by a
   scaling factor it worked out at boot when it calibrated the TSC frequency.
   `CLOCK_REALTIME` is that count plus an offset for "what time was it at
   boot", which comes from the battery-backed RTC, itself driven by its own
   little 32 kHz crystal with its own error.

Every stage carries a rate error: the crystal's physical error, temperature,
and the kernel's estimate of the TSC frequency, which is a measurement made
over a fraction of a second at boot and can easily be off by more than the
crystal itself. The sum on pancake0 is roughly 350 ppm slow.

## What ppm means

Parts per million, used here as a *rate* error: how many microseconds the
clock gains or loses per second. 350 ppm slow means 350 us lost every second.

| Interval | Error at 350 ppm |
|---|---|
| 1 s | 0.35 ms |
| 1 min | 21 ms |
| 1 h | 1.26 s |
| 1 day | 30 s |
| 16 days (uptime on 2026-10-05) | about 8 min |

For scale: a quartz wristwatch is ~20 ppm, a typical server crystal <100 ppm,
the 25 MHz class parts used on PC boards ±50 ppm as specified. NTP's slew
correction is capped at 500 ppm, so 350 fits with little margin; if the
`time_sync` health check shows the offset growing again with NTP enabled, the
TSC calibration is worse than that and chrony is the next tool.

## What NTP actually changes

Nothing physical. The crystal keeps ringing at whatever frequency it has.
systemd-timesyncd measures the error against an NTP server and, through
`adjtimex`, changes the **kernel's scaling factor**: each TSC tick is counted
as very slightly more (or fewer) nanoseconds, so the software clock ticks at
the right rate even though the hardware does not. Two mechanisms:

- **Step**: a large error (the 8 minutes) is fixed once by jumping
  `CLOCK_REALTIME`. Done only at the first synchronization; must happen
  between recordings because `phc2sys -rr` passes the jump to the camera
  clocks (see `ptp_services.md`).
- **Slew**: small errors are removed by running the clock a little fast or
  slow until they are gone, and the measured rate error is kept as a standing
  frequency correction so the clock stops drifting *between* polls. Polling
  continues for the life of the machine, every 32 s to ~34 min.

While synchronized the kernel also writes the corrected time to the RTC every
11 minutes, so the next boot starts right.

## Which clock to read in code, and what changes

Nothing about *how* programs ask for time changes. The Orange and citrus code
already use steady clocks for intervals and the system clock for labels
(checked 2026-10-05: hundreds of `steady_clock` uses, dozens of
`system_clock` / `CLOCK_REALTIME` / `datetime.now()`, a few raw `rdtsc`).
What changes is what each clock returns:

| Clock | C/C++ | Python | Before NTP | After NTP |
|---|---|---|---|---|
| Wall clock | `std::chrono::system_clock`, `CLOCK_REALTIME`, `gettimeofday` | `time.time()`, `time.time_ns()`, `datetime.now()` | drifted ~30 s/day; 8 min behind | one step forward at first sync, then within ms of real time. Use for filenames, run records, anything compared with the outside world. |
| Monotonic | `std::chrono::steady_clock`, `CLOCK_MONOTONIC` | `time.monotonic()`, `time.monotonic_ns()` | never jumps; intervals 350 ppm long | never jumps; same rate correction applied, so intervals become slightly more accurate. Use for durations, timeouts, frame pacing. No code change. |
| Raw hardware rate | `CLOCK_MONOTONIC_RAW`, `rdtsc` | n/a | uncorrected | still uncorrected: NTP never touches these. Only for code that wants the TSC exactly as the CPU counts it. |
| Camera timestamps | EVT SDK frame timestamps, from the NIC PHC via PTP | | consistent with each other and with the host, 8 min from wall time | follow the system clock through `phc2sys -rr`: comparable with `CLOCK_REALTIME` on the host and with other synchronized machines. |

`std::chrono::high_resolution_clock` is an alias for `system_clock` in libstdc++,
so treat it as wall clock: it will see the step. A duration computed from two
wall-clock readings that straddle the step is wrong by the step size; that is
the one hazard, and the reason to enable NTP between recordings.

## Recordings made before NTP was enabled

Their internal timing is fine: it came from steady clocks and the PTP chain.
Their wall-clock labels are early by an amount that grew from an unknown
offset at the 2026-09-19 22:22 boot to ~8 min on 2026-10-05, at roughly
30 s/day. There is no exact record of the offset at any given recording;
`rig_health_check.py`'s `time_sync` check now logs it every five minutes
(`/var/lib/rig-health/history.jsonl`), so from here on it is known.
