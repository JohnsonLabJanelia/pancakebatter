# Driver 610 post-boot acceptance, 2026-09-19

Boot ID: `b9bc5853-4478-4219-b890-b8bc9e09d1db`.
Durable evidence: `/mnt/Data2/nvenc-driver-postboot-20260919-b9bc5853`.

## Passed host and basic CUDA checks

- Running kernel remains `6.5.0-44-generic`.
- All nine GPU UUIDs, indices, names, and PCI addresses match the saved 535
  preflight inventory; every GPU reports `610.57.04`.
- Both loaded NVIDIA and nvidia_peermem report `610.57.04`. The peer-memory
  module links to `nvidia,ib_core`; OFED/openibd is active. Legacy nv_peer_mem
  is not loaded. DKMS lists the expected NVIDIA and existing OFED registrations.
- All 22 installed package versions/architectures match the frozen lock, and
  dpkg audit is clean.
- `/usr/local/cuda` still resolves to `/usr/local/cuda-12.2`, and the
  TensorRT 10.0.1.6 directory remains present.
- The installed CUDA 12.2 `extras/demo_suite/deviceQuery` reports PASS and
  driver API/runtime versions `13.3 / 12.2`.
- The installed CUDA 12.2 `extras/demo_suite/vectorAdd` reports Test PASSED
  independently on each of the nine GPUs, selected by CUDA_VISIBLE_DEVICES UUID.
- Inspected boot logs contain no NVRM Xid or unknown-symbol report. The failed
  rpc-svcgssd and systemd-networkd-wait-online units also failed on the preceding
  boot, with the same missing NFS credentials and network wait timeout.

## Desktop and application acceptance completed

The user restored `graphical.target` and GDM. Direct NVIDIA OpenGL rendering,
EVT GPUDirect acquisition, two-camera PTP headless real TensorRT/NVENC, and
two-camera GUI recording now pass. Headless encoded 602 frames per camera;
GUI encoded 1,002 per camera, with valid real-content videos and zero drops
or encode failures. See the [runtime acceptance record](nvidia_driver_runtime_acceptance_20260919.md)
for evidence, repeatable inputs, and the two headless metadata caveats.

The original post-boot snapshot remains an immutable record of the earlier
host-check stage. Runtime evidence is separately preserved at
`/mnt/Data2/nvenc-driver-runtime-acceptance-20260919`.
The isolated CUDA 13.1/native-NV12 experiment is next; production remains on
CUDA 12.2. No NVENC tiling-cost improvement has been measured on driver 610.
