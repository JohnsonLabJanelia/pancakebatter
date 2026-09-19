# Isolated CUDA 13.1 toolkit for the NVENC layout probe

The NVENC experiment uses a small, user-local CUDA 13.1.1 toolkit at
`/home/jeremy/.local/opt/cuda-13.1.1-nvenc`. It was staged on 2026-09-19 from
NVIDIA's official redistributable archives. Production `/usr/local/cuda` still
resolves to `/usr/local/cuda-12.2`; no system package, shell startup file,
ldconfig setting, CUDA alternative, or driver was changed.

This subset supplies `cuda_nvcc`, `cuda_crt`, `cuda_cudart`, `cuda_cccl`, and
`libnvvm`. It omits math libraries and profilers from the toolkit subset. A separate
user-local Nsight Systems is used for the native-array comparison because the
production CUDA 12.2 profiler cannot import the newer CUDA API trace completely. The compiler is 13.1.115 and the runtime is 13.1.80; these are
the component versions in the CUDA 13.1.1 release. Installed NVIDIA driver
610.57.04 supplies the driver API library (`libcuda.so.1`).

## Reproduce

Run as the normal user, choosing a new prefix:

```bash
python3 stage_cuda_probe_toolkit.py \
  --prefix /home/jeremy/.local/opt/cuda-13.1.1-nvenc \
  --cache /tmp/nvenc-cuda131-downloads
```

The default lock is `configs/cuda_probe_toolkit_13_1_1.json`. It pins the
upstream manifest SHA-256 and each component URL, size, and SHA-256. The
stager verifies cached files too, requires a new prefix, extracts with Python's
tar data filter, rejects conflicting merged files, retains component licenses,
and audits staged symlinks before publishing the final prefix. It rewrites the
two verified upstream pkg-config files to relocatable include/lib paths. Cache
validation rejects non-regular files; downloads use unique temporary paths and
atomic no-replace publication. It refuses root.
Use Python with `tarfile.data_filter` support (the host's patched Python 3.10
has this). The completed prefix includes its lock and NVIDIA manifest.

Use explicit compiler/include/library paths for the probe. Do not add this
prefix to global PATH or LD_LIBRARY_PATH or redirect `/usr/local/cuda`.
The normal user can stage this toolkit without sudo, reboot, or desktop shutdown.
This is a maintained experiment recipe, not a replacement for the repository's
production CUDA installation procedure.

The 12 focused stager tests pass with:

```bash
python3 -m unittest discover -s tests -p test_cuda_probe_toolkit.py
```

## Compiler/runtime validation

The independent smoke source is `tests/cuda_probe_runtime_smoke.cu`. Build it
with the local compiler and an explicit runtime search path:

```bash
/home/jeremy/.local/opt/cuda-13.1.1-nvenc/bin/nvcc \
  -std=c++17 -arch=sm_86 --cudart shared \
  -Xlinker -rpath -Xlinker /home/jeremy/.local/opt/cuda-13.1.1-nvenc/lib \
  tests/cuda_probe_runtime_smoke.cu -o /tmp/cuda131_runtime_smoke
CUDA_VISIBLE_DEVICES=GPU-c37c3690-5fbc-361c-5a77-8c525a47840f \
  /tmp/cuda131_runtime_smoke
```

On the idle A16 GPU 1 this passed with
`PASS runtime=13010 driver=13030 values=4096`. `ldd` resolved libcudart.so.13
inside the new prefix. This proves compilation, runtime loading, kernel
execution, and result correctness; it does not prove the NVENC native-array path.

The new headers define `CU_AD_FORMAT_NV12`,
`CUDA_ARRAY3D_VIDEO_ENCODE_DECODE`, and `cuArrayGetPlane`. The standalone probe
uses NVIDIA's Video Codec SDK 13.1 interface headers. Its 2026-09-19 acceptance
ran linear and native-array NV12 input, both per-frame and prefilled, for 600
frames per mode at 4512x4512 and 100 fps. All four streams decoded completely,
sampled Y planes matched the source exactly with neutral UV, their bitstreams
were identical, and the Nsight traces covered the expected CUDA copies through
resource teardown. The retained artifact is
`/mnt/Data2/nvenc-native-nv12-investigation-20260919`.

That result accepts the isolated compiler/runtime and standalone native-array
experiment. It does not install CUDA 13.1 system-wide, change production
`/usr/local/cuda-12.2`, or by itself validate native-array input in Orange's
external recorder.

Source references: [CUDA 13.1.1 component manifest](https://developer.download.nvidia.com/compute/cuda/redist/redistrib_13.1.1.json),
[NVIDIA redistributable archive instructions](https://docs.nvidia.com/cuda/archive/13.1.1/cuda-installation-guide-linux/index.html#tarball-and-zip-archive-deliverables),
and [NVENC 13.1 programming guide](https://docs.nvidia.com/video-technologies/video-codec-sdk/13.1/nvenc-video-encoder-api-prog-guide/index.html).
