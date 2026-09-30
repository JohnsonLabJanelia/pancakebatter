# Frozen NVIDIA driver package set for pancake0

This is the 22-package R610 preparation set. Downloading these files does not
install them. The native-architecture payload total is 405,137,980 bytes.
The installed driver remains 535.183.06 until a separate maintenance phase.

Most driver components use `610.57.04-1ubuntu1`. The DKMS tool and four EGL
integration packages have their own version schemes; those versions are
also pinned. All payloads are `amd64` except the architecture-independent
DKMS and version-pinning packages. The exact filenames, repository URLs,
SHA-256 hashes and sizes are recorded in the frozen package lock.

| Package | Exact version | Purpose |
| --- | --- | --- |
| `cuda-drivers` | `610.57.04-1ubuntu1` | Selects the NVIDIA driver dependency set. It does not install a CUDA toolkit. |
| `dkms` | `1:3.2.1-1ubuntu1` | Builds registered modules for the selected kernel; upgrades the shared DKMS tool from 2.8.7 to 3.2.1. |
| `libnvidia-cfg1` | `610.57.04-1ubuntu1` | GPU configuration library required by driver services. |
| `libnvidia-compute` | `610.57.04-1ubuntu1` | CUDA driver API and compute/management libraries used by applications. |
| `libnvidia-decode` | `610.57.04-1ubuntu1` | NVIDIA video-decoding library. |
| `libnvidia-egl-gbm1` | `1.1.4-2ubuntu1` | EGL integration with Generic Buffer Management. |
| `libnvidia-egl-wayland21` | `1.0.2-1ubuntu1` | EGL integration with Wayland. |
| `libnvidia-egl-xcb1` | `1.0.6-1ubuntu1` | EGL integration with X11 through XCB. |
| `libnvidia-egl-xlib1` | `1.0.6-1ubuntu1` | EGL integration with X11 through Xlib. |
| `libnvidia-encode` | `610.57.04-1ubuntu1` | NVENC user-space library used by the recorder. |
| `libnvidia-fbc1` | `610.57.04-1ubuntu1` | NVIDIA framebuffer-capture library required by the driver package set. |
| `libnvidia-gl` | `610.57.04-1ubuntu1` | OpenGL/EGL/Vulkan graphics driver libraries. |
| `libnvidia-gpucomp` | `610.57.04-1ubuntu1` | Driver-side GPU compilation support. |
| `nvidia-dkms` | `610.57.04-1ubuntu1` | Registers/builds the NVIDIA module sources through DKMS. |
| `nvidia-driver` | `610.57.04-1ubuntu1` | Collects the matching kernel, compute, graphics, and video driver components. |
| `nvidia-driver-pinning-610.57.04` | `610.57.04-1ubuntu1` | Installs NVIDIA version-locking preferences for the selected driver release. |
| `nvidia-firmware` | `610.57.04-1ubuntu1` | GPU firmware files loaded by the driver at runtime. |
| `nvidia-kernel-common` | `610.57.04-1ubuntu1` | Driver support files, module configuration, and integration hooks. |
| `nvidia-kernel-source` | `610.57.04-1ubuntu1` | Source and binary objects used to build the proprietary kernel modules. |
| `nvidia-modprobe` | `610.57.04-1ubuntu1` | Helper for loading NVIDIA modules and creating device nodes. |
| `nvidia-persistenced` | `610.57.04-1ubuntu1` | Service that can keep the driver initialized between applications. |
| `xserver-xorg-video-nvidia` | `610.57.04-1ubuntu1` | NVIDIA Xorg display driver for the workstation desktop. |

Production CUDA 12.2, `/usr/local/cuda`, TensorRT 10.0.1.6, MLNX_OFED 24.01,
EVT, Rivermax, and kernel 6.5.0-44 remain outside this transaction. A separate
CUDA 13.1 toolkit for the native-NV12 experiment comes after driver acceptance.

The current `dkms=2.8.7-2ubuntu2` package is saved separately for rollback.
It is not a twenty-third package to install with R610. The verified 535.183.06
runfile and the checked Clonezilla image remain the other recovery layers.

The main qualification risk is the combination of newer driver/DKMS with the
existing kernel and OFED camera path. Package checksums prove file integrity;
module builds and real camera/inference tests establish compatibility.

The native profile omits 32-bit NVIDIA compatibility libraries and the optional
`nvidia-settings`/`nvidia-xconfig` tools that the old runfile installed. Their
omission is a maintenance scope decision, not a claim that the applications
can never be needed. Jeremy confirmed on 2026-09-19 that there are no known
32-bit GPU applications; keep this native package profile. No mixed-version
compatibility libraries will be improvised.
