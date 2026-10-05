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

## The slowest element sets the loop; the bus rarely is it

A closed loop cannot be faster than its slowest stage, so before choosing a
DAQ interface, find that stage. The ladder above only matters when *both* ends
of the loop are electrical: an analog threshold in, a TTL out. Most
experiments have a sensor integration time or a display refresh in the loop,
and those are 10 ms-class no matter what sits between them.

**Signal that only exists after computation (an image).** The decision has
to be made on the host, and the time budget is:

    exposure + sensor readout + transfer + compute + output quantum

For a microscope or camera at 10-100 Hz the exposure and readout alone are
10-100 ms, so a few ms of USB latency on the output side is a small fraction.
What makes an image-based loop good is not the bus but: a pipeline that
never drops or queues frames (the Orange pattern: DMA into GPU memory, compute
on the GPU, threads on isolated cores, bounded work per frame); an output
that can act as soon as the decision exists (a DIO or analog-out update, not
a display); and recording when each frame was *exposed* (camera/PTP timestamp
or the sensor's exposure-out TTL into the DAQ) and when the output *changed*
(a DIO toggled at the command, the actuator's own monitor signal), so the
loop latency is measured per trial. Typical result: one to two frame periods,
deterministic to a fraction of a frame. If that is too slow, the fix is a
faster sensor path (sub-frame region readout, line scan) or a different
signal that hardware can see (a photodiode or PMT analog output instead of
the reconstructed image), not a faster bus.

**DAQ signal in, display change out (update grating orientation when a
channel does X).** The output is a display, and a display only changes at
its refresh: 16.7 ms at 60 Hz, 4.2 ms at 240 Hz, plus one or two frames of
pipeline inside the GPU and monitor. The USB DAQ's 2-5 ms input latency is
smaller than one refresh quantum, so it is not the bottleneck. Implementation
that works:

1. Stream the DAQ with small transfers (about 1 ms of samples per block) into
   a dedicated thread that evaluates the condition on every block.
2. Hand the decision to the rendering loop, which applies it at the next
   vsync (vsync-aware stimulus software; a high-refresh monitor shrinks the
   quantum).
3. Record the proof: the DAQ channel itself, a DIO the stimulus code toggles
   when it decides, and a photodiode on the screen so the moment the grating
   actually changed is on the same sample axis. Expect 10-30 ms from signal
   to pixels, repeatable to about one frame.

If the condition is a simple threshold, the DAQ's hardware trigger or counter
can turn it into a digital edge with microsecond latency, but the display
still only changes at the next refresh, so it buys reliability rather than
speed.

**When PCIe (or a microcontroller/FPGA) is actually required.** When the
input is electrical and fast, the output is electrical and fast, and the
rule must fire in well under a millisecond: spike-triggered stimulation,
event-triggered galvo or AOM steering, hardware gating. Then every
millisecond-scale element has to be designed out of the loop, and USB is one
of them.

## Audio stimuli: aligning tones delivered to the animals

The software timestamp of `play()` is the least reliable number in the whole
chain. A desktop audio stack (PulseAudio, PipeWire in its default
configuration) buffers 20-100 ms and the delay varies per call; a tuned
low-latency path (ALSA `hw:` device directly, or JACK/PipeWire with a small
quantum) gets to 1-10 ms but still jitters; a USB audio interface adds a
fixed, device-specific few ms on top. None of that is knowable from the host.
So, as with everything else: do not infer onset from software, record it.

Three ways, best first:

1. **Make the DAQ the sound source.** Most USB DAQs have analog outputs with
   hardware-timed buffers at 10-100 kS/s, enough for tones up to several kHz.
   Preload the waveform and start it on the DAQ's own clock; the output's
   first sample is then on the same sample axis as the inputs, so onset is
   known to the sample with zero software uncertainty, and the strobe fit
   ties it to the cameras' PTP time. The DAQ's output can only drive a small
   load, so put a small audio amplifier between it and the speaker; the
   amplifier's delay is microseconds. Loop a copy of the amplifier output
   back into an analog input anyway (resistor divider), as the record of what
   was actually delivered.
2. **Sound card plus a sync track.** If the stimulus needs a real sound card
   (many channels, high sample rate, existing stimulus software), use its
   stereo nature: left channel carries the tone to the speaker, right channel
   carries a short click or a step at the same instant into a DAQ analog
   input. The two channels of one sound card are sample-locked by its own
   clock, so the recorded click marks the tone's onset exactly, unaffected by
   the software path, the room, or the water. Also record a copy of the
   speaker drive signal.
3. **Record the sound itself.** A microphone (or in water, a hydrophone or an
   accelerometer on the tank) into a DAQ channel captures the stimulus as the
   animal received it, including the transducer's rise time and the tank's
   acoustics. Good as the ground truth of delivery; noisier as a timing marker
   than 1 or 2, so use it alongside them, not instead.

Physical delays to keep in view: sound travels 0.34 m per ms in air and
1.48 m per ms in water, so tank-scale distances are sub-millisecond; small
speakers and transducers take a few ms to reach full amplitude, so define
onset as the start of the ramp (use a 5 ms cosine ramp to avoid broadband
clicks) and let the recorded copy show the rest. Conditioning paradigms work
on tens to hundreds of ms between cue and outcome, so millisecond alignment
is ample; it matters more when measuring the animal's response latency to the
tone.

Fish specifics. Zebrafish hear roughly 100 Hz-4 kHz with best sensitivity
around 500-1000 Hz, and larvae respond strongly to acoustic/vibrational
startle. A speaker in air couples poorly into water (impedance mismatch; most
of the energy reflects at the surface), so expect an underwater transducer or
a vibration exciter bolted to the tank to work far better than a speaker above
it. Fish sense particle motion as much as pressure, and a small tank has its
own resonances that reshape any tone, so measure what the tank delivers (a
hydrophone for pressure, an accelerometer on the tank wall for motion) rather
than trusting the waveform you sent. Timing alignment and loudness/spectrum
calibration are separate problems; the recording channel in 1-3 solves the
first and gives you the data for the second.

## Calibrating tank loudness with a hydrophone

Timing tells you *when* the tone happened; this tells you *how loud it was
where the fish is*, which cannot be inferred from the voltage you sent.

**Units first, because underwater acoustics uses different ones.** Sound
pressure level in water is quoted in dB re 1 uPa; in air it is dB re 20 uPa,
and the two media have very different impedances, so an air SPL number cannot
be compared with a water one at all. A "120 dB" tone in a tank is not loud
the way 120 dB in a room is. Fish also sense *particle motion*, which in the
near field of a small tank is not proportional to pressure; if the paradigm
depends on it, measure acceleration too (an accelerometer on the tank wall or
a small waterproof one at the fish position, in m/s^2 or dB re 1 um/s^2).

**Equipment.** A hydrophone with a stated receive sensitivity, typically
-180 to -210 dB re 1 V/uPa (that is, a few uV to tens of uV per Pa), so it
needs a low-noise preamplifier with a known gain; a DAQ analog input with
enough range and a sampling rate at least 2.5x the highest stimulus frequency
(10 kS/s covers tones to 4 kHz); the stimulus path exactly as used in
experiments (same DAQ output or sound card, amplifier, transducer, tank, water
level). Trust the manufacturer's sensitivity curve, and if a reference source
(pistonphone or a calibrated projector) is available, check one frequency
against it.

**Procedure.**

1. Place the hydrophone where the fish will be, same depth, with the dish or
   arena in place. Record 10 s of silence: this is the noise floor, and it is
   usually dominated by pumps, filters, lights and the building. The stimulus
   must sit well above it; fix the noise before calibrating if it does not.
2. For each stimulus frequency (and every one you might use later), play a
   steady tone of a few seconds at several output amplitudes spanning the
   range you intend to use. Drive and record through the DAQ so the played
   and recorded waveforms share one sample axis.
3. From each recording take the RMS of the band-passed signal (or the FFT bin
   at the tone frequency, with a window), convert volts to pressure with the
   preamp gain and hydrophone sensitivity, and express it as dB re 1 uPa:
   `SPL = 20*log10(p_rms / 1e-6 Pa)`.
4. Plot SPL against output amplitude per frequency. It should be a straight
   line in dB; where it bends, the amplifier or transducer is saturating. Also
   look at the spectrum for harmonics: a small speaker driven hard delivers a
   good part of its energy at 2f and 3f, which a fish hears as a different
   stimulus.
5. Repeat at a few positions across the arena. A small tank has strong
   resonances, so the level at a given frequency can vary by 10-20 dB over a
   few centimetres; either choose frequencies and positions where it is flat,
   or accept and document the map.
6. Store the result as a per-rig calibration table (frequency -> output
   amplitude per target SPL, plus noise floor, plus date and water level) next
   to the host config, and redo it whenever the transducer, amplifier, tank,
   water level or arena changes. Keep the raw recordings.

Keep the hydrophone (or at least the loopback channel) connected during
experiments: the recorded copy of every tone is the evidence that the
calibration still held, and it makes level a measured quantity per trial
rather than an assumption.

Reference points: published zebrafish auditory thresholds are in the low
hundreds of dB re 1 uPa with best sensitivity around 500-1000 Hz, and
startle thresholds are higher than detection thresholds; take the numbers for
the paradigm from the literature, then confirm what *this* tank delivers with
the procedure above rather than from the amplifier's volume knob.
