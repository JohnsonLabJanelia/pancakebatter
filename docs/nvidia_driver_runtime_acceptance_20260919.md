# Driver 610 runtime acceptance, 2026-09-19

The proprietary 610.57.04 migration passed host, CUDA, EVT GPUDirect,
two-camera headless recording/real TensorRT, and GUI recording acceptance.
Kernel 6.5.0-44, OFED 24.01, TensorRT 10.0.1.6, and production CUDA 12.2
remain in place. GDM is active and the default target is graphical.target.
The desktop reports direct NVIDIA RTX A6000 OpenGL rendering on 610.57.04.

Original evidence: `/tmp/nvenc-driver-acceptance-20260919T222953Z`.
Durable copy: `/mnt/Data2/nvenc-driver-runtime-acceptance-20260919`.
See the copy's `archive-manifest.json` and `SHA256SUMS` for file verification.
Historical JSON absolute paths intentionally retain their original locations.
The earlier host-check evidence remains at
`/mnt/Data2/nvenc-driver-postboot-20260919-b9bc5853`.

## Results

| Check | Result |
| --- | --- |
| CUDA 12.2 | deviceQuery passes; vectorAdd passes on all nine GPUs |
| EVT GPUDirect, camera 2010096 | 501 frames, 100.017 fps, zero timeouts/gaps |
| Headless two-camera timed recording | 602 received/ACKed/encoded frames per camera; zero skips/drops/failures |
| GUI two-camera timed recording | 1,002 received/ACKed/encoded frames per camera; zero skips/drops/failures |
| Content and parity | Both runs have valid real-content 4512x4512 HEVC videos and packet/frame/metadata parity |
| GUI validation | Pass, zero warnings; timing/display checks pass; about 60 GUI fps |

Both runs use 100 fps PTP/TwoStep, register-read decimation 100, and AQ and
temporal AQ off. Current configuration is `100_cam4_ptp_fourcam`, selecting
only 2010095 (analytics GPU 7, recorder shards 7/8) and 2010096 (analytics GPU
5, shards 5/6). The old `100_cam4_ptp` folder does not exist on this host.

Headless results are in `supervised-timed/`. The recording is 6.02 seconds;
after recording frame 50, acquisition-to-detect p95 is 2.649/2.600 ms for
2010095/2010096, with queue-wait p95 0.015/0.018 ms. Camera acquisition has
zero frame gaps, GetFrame errors, preprocess drops, and IPC failures/timeouts.

GUI results are in `gui/recordings/2026_09_19_18_35_17/`. The recording is
10.02 seconds at 152.232/152.121 Mbps; acquisition-to-detect p95 is
2.948/2.846 ms. `gui/validation.json` passes with zero warnings. The separate
external session verifier also passes, recorded in
`gui/external-verification-after-video-sanity-result.json`. Its first attempt
required standalone content-report sidecars; the existing
`scripts/external_video_sanity.py` generated those reports from the actual
MP4s before the successful repeat. No validation gate was relaxed.

Real TensorRT inference ran in both tests. Camera 2010095 had positive
detections and 2010096 had zero detections; these checks do not establish
model quality. GUI crop recording was disabled; pose/crop behavior is outside
this acceptance. These short smoke measurements do not establish a sustained
latency bound or a causal performance improvement from the driver change.

## Preserved diagnostic failures and application caveats

The first manual two-camera runner failed the current producer/recorder session
identity contract before encoding. The first supervised attempt encoded
successfully but omitted the explicit timed lifecycle needed for Orange to
write `recording_session.json`. Both attempts remain in the evidence tree.
The accepted specimen uses supervised recorders and
`fixed.recording_control.record_for_seconds = 6`. No application code or
binary was changed during driver acceptance.

Two existing headless metadata inconsistencies remain: the completed manifest
reports `actual_recording_duration_s = 0.0` despite elapsed time about 6.018 s
and 6.02-second videos; some `runs.csv` snapshot counters lag by one while
final recorder summaries and manifest parity correctly report 602. Current
validators do not reject these fields. They are application follow-up work,
not evidence of a driver or recording failure.

## Repeat the accepted shape

Use `supervised-timed/spec.json` and `supervised-timed/command.json` in the
archive as the exact accepted specimen. It derives from the repository's
`2010095_2010096_headless_real_yolo_aq_off_100_cam4_ptp_external_ipc_supervised.json`.
For a new run, create a new specimen with a unique experiment/session ID,
output roots, all per-stream artifact paths, and socket paths. Preserve
`supervise_processes = true`, explicit `recorder_tool_path`, current camera
selection/GPU mapping, duration 12 s, warmup 1 s, and timed recording 6 s.
Invoke it with the narrow `orange-local-benchmark` wrapper and the recorded
early-owned-frame, ready-event-fastpath, and detach-input flags. Require the
application-written completed manifest and all existing verification gates.
Do not rerun the old manual runner as the current driver acceptance recipe.

For GUI acceptance, `gui/launch-env.json`, `gui/app-config.json`, and
`gui/validation-command.json` record the exact inputs. A temporary direct child
of `orange_data/config/local` containing only the two selected camera JSONs
limits runtime camera selection; `ORANGE_GUI_EXPECT_CAMERAS` alone only checks
preflight. Use an app-config copy with a unique recording root and all local,
canonical, and run latest-pointer destinations disabled. The temporary camera
folder was checksum-verified, preserved under `gui/config/`, and removed;
production camera/app configs were verified unchanged (`gui/config-cleanup.json`).
Use the explicit new artifact path when validating, rather than a selector
that could skip a failed latest attempt. Display authorization used the
existing Xauthority cookie; no xhost access change was needed.

## Next isolated NVENC experiment

CUDA 13.1 has not been installed and no native-NV12 result is claimed. Plan a
toolkit-only installation alongside 12.2, keeping Orange and `/usr/local/cuda`
on 12.2. The installed driver can support the isolated probe; no further
driver replacement, desktop shutdown, or reboot is expected for this step.
Administrative authentication may still be needed and can be supplied from a
remote terminal. NVIDIA documents [side-by-side toolkit installations](https://docs.nvidia.com/cuda/archive/13.1.0/cuda-installation-guide-linux/index.html#other-package-notes).

First profile ordinary linear NV12 input on 610, then compare compatible native
NV12 CUDA-array input. NVIDIA documents that compatible block-linear arrays
can bypass the usual NVENC layout conversion in its
[NVENC programming guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-video-encoder-api-prog-guide/index.html).
Measure camera Mono8-to-surface preparation plus encoding: moving the work
into an earlier copy is only a win if total cost or contention improves.
Image dimensions and orientation remain unchanged. The old 535 trace's two
conversion launches took about 207 and 410 microseconds of GPU elapsed time;
a CUDA Graph would not remove that memory work or fuse the kernels. Their
individual roles and support for capturing NVENC's internal work are unproven.
