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

## A better reference: GPS-disciplined clocks

Each GPS satellite carries atomic clocks and broadcasts the time; a receiver
finds its position by comparing arrival times from four or more satellites,
which only works if it also solves for the exact time, so every position fix
is a time fix to tens of nanoseconds. A GPS-disciplined oscillator (GPSDO)
keeps a good local crystal ticking smoothly and nudges its frequency so the
ticks line up with satellite time: crystal smoothness short term, atomic
accuracy long term, in a small box with a 1 pulse-per-second output and
usually PTP/NTP out of the back. The NTP pool is largely fed by these.

For this rig a GPSDO acting as PTP grandmaster on the camera network would
make the host a PTP *client* (phc2sys direction flipped) and take the host
crystal out of the chain entirely. Only worth it if recordings must line up
with instruments outside the room at better than NTP's few milliseconds.

## Long recordings: drift is measured, never prevented

Drift is a rate, so it is harmless over a minute and seconds over a day. The
approaches, weakest to strongest; serious installations use the last three
together:

| Approach | What it gives | Limits |
|---|---|---|
| One clock for everything (shared 10 MHz / sample clock, PXI backplane) | no relative drift at all | hard across vendors or distance |
| Continuous discipline (NTP over the network, PTP on the LAN) | every clock slewed toward one reference while running | rate changes slightly as it is corrected; must never step mid-run |
| Record a common marker in every stream, fit afterwards | mapping between timelines recovered from the data itself; a day of once-per-second edges fits the relative rate to <1 ppm and shows wander in the residuals | needs a spare channel; needs the fit done |
| Hardware timestamps at the edge (the capturing device stamps from a disciplined clock) | the timestamp is made where the event happens, not where the data lands | needs devices that can do it (the camera NICs do, via PTP) |

The reason for the redundancy: the failure that matters in a long run is not
steady drift but a clock that steps, glitches or loses its reference partway
through, and only an independent signal recorded *in the data* lets you find
and repair that later.

## Worked case: a USB DAQ next to PTP-timed cameras and stimulus software

Setup: cameras and stimulus software are aligned through PTP timestamps that
follow the host system clock; a USB DAQ streams samples into the same host.

What the DAQ really gives you: samples taken at intervals set by *its own*
crystal (typically 20-50 ppm), delivered over USB in buffers that arrive
milliseconds late with jitter. The host can timestamp a buffer's arrival, not
a sample. So the DAQ timeline is `sample_index / nominal_rate` plus an
unknown start offset, drifting against the host at the DAQ crystal's ppm
(1-4 s per day). Host-side timestamps on buffers are only good to the
millisecond and only tell you about USB, not about the samples.

**The common pulse does not need a generator, and it does not demote the
server.** The cameras already emit a strobe on their GPO line once per
exposure (Orange configures it: `strobe_output_connection`, `gpo_index`).
Those cameras are PTP clients of the host, so every strobe edge *is* an event
on the server's timeline, and every frame already carries that edge's PTP
timestamp in its metadata. Wire the strobe into a DAQ digital or analog
channel and the DAQ record contains copies of the server's timeline, one per
frame. The pulse is a measurement channel, not a clock; the host stays the
source of truth and the DAQ becomes a device that records evidence of it.
An external TTL generator would only add a third free-running clock to fit.

Procedure (per recording):

1. Record the strobe on a DAQ channel sampled at ≥10x the frame rate, or
   use the DAQ's edge-counter / change-detection input if it has one.
2. Detect rising edges → sample indices `s_k`. Take frame timestamps `t_k`
   (PTP, from the camera metadata) in order; the counts match within a run,
   and a dropped frame shows up as a gap in `t_k` spacing with no matching gap
   in `s_k`, or vice versa.
3. Fit `t = a + b * s`. `b` is the DAQ's true sample period as seen by the
   PTP timeline; `(b * nominal_rate - 1)` is the DAQ crystal's ppm error,
   which should be constant across runs. Residuals are jitter plus any USB
   buffer glitch (a step in the residuals = samples lost; check the driver's
   sample counter).
4. Convert any DAQ sample to PTP time with the fit. Store `a`, `b`, residual
   RMS and max with the recording.
5. Stimulus events timestamped by software with `CLOCK_REALTIME` are already
   on the same timeline (phc2sys -rr), so they compare directly with the fitted
   DAQ time. But a software timestamp is when the stimulus was *commanded*;
   display and audio pipelines add tens of milliseconds. Record the stimulus
   itself into the DAQ too (photodiode on the screen, a copy of the audio
   line, or a DIO toggled by the stimulus code) and read onset off the DAQ.

The alternative direction, a DAQ counter output into the cameras' trigger
input, works the same way (the cameras timestamp the DAQ's pulses) but makes
the cameras externally triggered, which changes how Orange runs them. The
strobe-into-DAQ direction leaves the camera pipeline untouched.

## Latency is not timestamp accuracy

Two different questions hide in "how precise is my DAQ":

- **When did this sample happen?** Answered after the fact. A sample's position
  in the stream is exact (set by the DAQ crystal) no matter how late the USB
  block carrying it arrives, and the strobe fit anchors that position to PTP
  time. Microsecond-class *timestamps* from a USB device are routine. Latency
  and jitter do not degrade them at all.
- **How soon can the program react to it?** Bounded by the delivery latency,
  and worse, by its jitter: OS scheduling, the USB host controller's polling,
  driver buffering. Over USB on a general-purpose OS that is a few ms with
  small transfers and a tuned host; it is not sub-millisecond and not
  deterministic. USB audio interfaces, which use the reserved-bandwidth
  isochronous mode and are built for this, reach 1-3 ms round trip, which is
  about the floor for the bus.

Design consequence: never let the timestamp depend on the reaction path.
Record on the DAQ axis, align with the strobe fit, and treat host timestamps
as diagnostics. Then choose the reaction path by how fast the loop must be.

## How fast can a closed loop get?

Typical loop latencies (input event to output change), with the usual jitter,
for the ways a reaction can be built. Figures are order-of-magnitude and
assume a competent implementation.

| Path | Latency | Jitter | Notes |
|---|---|---|---|
| Camera frame -> software -> stimulus display | 20-50 ms | ~one frame | Bounded by exposure + readout + transfer + inference + the display's own refresh pipeline (16 ms at 60 Hz). Camera-based loops are 10 ms-class at best regardless of software; at 100 fps the frame period alone is 10 ms. |
| USB DAQ -> user program on a normal Linux host -> USB DIO out | 2-10 ms | 1-5 ms | Small transfer sizes and a quiet machine get toward 1-2 ms. Fine when the next stage is a display or an animal's reaction time (tens of ms). |
| PCIe DAQ, DMA into host memory, user-space polling on an isolated core (the Orange/Rivermax pattern) | 10-100 us | tens of us | What this rig already does for frames: the NIC DMAs into GPU memory, threads spin on `isolcpus` cores. A `PREEMPT_RT` kernel bounds the worst case at a few tens of us; stock kernels have rare ms-scale outliers. |
| Microcontroller with its own ADC (Teensy 4.x class, 600 MHz, bare metal) | 1-10 us | <1 us | Deterministic because there is no OS. Reads a pin or ADC, compares, drives an output. The right home for a reflex; the host configures it and records what it did. |
| FPGA / commercial real-time processors (DSP or FPGA based electrophysiology systems) | <10 us, often <1 us | ns | Spike detection, filtering and the output decision all in logic. This is how "detect spike, fire laser" products specify loop times in the tens of us. |
| Analog comparator / hardware trigger line | ns-us | ns | A threshold crossing drives a TTL directly. No computation, but unbeatable when the rule is simple. |

Physical limits that set what "fast enough" means for "spike detected, fire
laser within 500 us":

- A spike waveform lasts ~1 ms. Detecting it on the rising phase with a
  threshold gives a decision 100-300 us after onset; waiting for the full
  shape to classify the unit costs the whole millisecond. So the 500 us budget
  is mostly spent before any electronics act, and only a hardware path
  (comparator, microcontroller, FPGA) leaves room for the rest.
- Laser diodes and LEDs with a proper driver switch in microseconds; mechanical
  shutters and some AOM drivers take milliseconds. Check the actuator before
  blaming the computer.
- Biology is slow by comparison: synaptic delays ~1 ms, axonal conduction
  tens of mm per ms, behavioral reaction 100+ ms. A loop that is well inside
  the relevant biological timescale is fast enough; faster buys nothing.

The pattern that works, and that this rig already partly follows:

1. **Reflexes in hardware.** Anything that must happen in <1 ms lives on a
   microcontroller, FPGA or comparator next to the signal, with the host
   setting parameters, not making the decision.
2. **Judgment on the host.** Decisions that can tolerate milliseconds (change
   the stimulus, start a trial, adapt a threshold) go through the DAQ/USB or
   camera path on the host, which has the full picture and the storage.
3. **Everything on one recorded axis.** The DAQ records the input, the
   hardware reflex's output, and a DIO line the host toggles whenever it issues
   a command. Loop latency and jitter are then *measured* in every recording,
   and the strobe edges tie that axis to the cameras' PTP time. Nothing about
   the alignment depends on how fast anything reacted.
