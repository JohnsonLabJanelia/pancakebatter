## HB-20000-SB Camera Datarate

The Emergent Vision Technologies HB-20000-SB camera uses a large Sony IMX531 Monochrome sensor with a 4512 x 4512 layout of pixels sized $2.74 \times 2.74 \mu\text{m}$ each. The data rate can be calculated using the following specific values:
- **Resolution**: 4512 pixels (width) $\times$ 4512 pixels (height)
- **Bit Depth**: 8 bits per pixel
- **Frame Rate**: 100 frames per second


The calculation is described in the [Datarate Calculation document](datarate_calculation.md#generic-form-of-data-rate-calculation):

```math
\text{Data Rate (bps)} = (4512 \times 4512) \times 8 \times 100 = 16,286,515,200
```

```math
\text{Data Rate (Gbps)} = \frac{16,286,515,200}{1,000,000,000} = 16.29 \, \text{Gbps}
```

This calculation shows that the data rate is approximately **16.29 Gbps** when the camera is at full resolution at 100 FPS.

## Disk Writing Without Compression

This camera has a substantial amount of data transmitted especially in the case where compression is not utilized. Refer again to the [Datarate Calculation document](datarate_calculation.md#disk-writing-calculation).

```math
\text{Data Rate (GBps)} = \frac{16.29}{8} = 2.04
```

```math
\text{Data Rate (GBpm)} = \frac{2.04}{60} = 122.4
```

Thus, you need to have a storage device that is capable of writing at least 2.04 GBps. Most NVMe storage you can find today is capable of doing this.

## Disk Writing With Compression

The software `orange` synchronizes the cameras in concert with one another and performs online video compression using either software encoders on CPU (H.264, H.265/HEVC) or hardware accelerated encoders via [NVIDIA's Video Codec SDKs](https://developer.nvidia.com/video-codec-sdk) and NVENC chips. As of 11/07/24, conducting thorough benchmarking of encoder performance with this camera on `pancake0` has not been performed.

### Disk Writing Benchmarking

Proper disk read/write performance has not been evaluated for these cameras as of writing (11/07/24) for the cameras at `pancake0`. The disks on the machine are described in the [pancake0_config.yml](../pancake0_config.yml) file in this repo.
