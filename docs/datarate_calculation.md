# Datarate Calculations
This document is meant to demonstrate the simple ways of calculating the datarates coming off cameras using servers built through pancakebatter. All values here assume no compression. Compression statistics will be shown in camera specific documents.

 ❤️ Jeremy Delahanty

## Generic Form of Data Rate Calculation

```math
\text{Data Rate (bps)} = \text{Width (pixels)} \times \text{Height (pixels)} \times \text{Bit Depth (bits per pixel)} \times \text{Frame Rate (frames per second)}
```

```math
\text{Data Rate (bps)} = W \times H \times D \times F
```

```math
\text{Data Rate (Gbps)} = \frac{W \times H \times D \times F}{1,000,000,000}
```

Where:
- $bps$: Bits per second
- $Gbps$: Gigabits per second
- $W$: Width in pixels
- $H$: Height in pixels
- $D$: Bit Depth in bits per pixel
- $F$: Frame Rate in frames per second

## Disk Writing Calculation
To calculate the amount of data written to disk in gigabytes per second (GBps), we use the following formula:

```math
\text{Data Rate (GBps)} = \frac{W \times H \times D \times F}{8 \times 1,000,000,000}
```

Or:

```math
\text{Data Rate (GBps)} = \frac{\text{Gbps}}{8}
```

Where:
- 8 is used to convert bits to bytes (8 bits per byte)
- $GBps$: Gigabytes per second

Calculating data rate per minute and hour is calculated from this value.

## Benchmarking your Disk Writing Speed

Proper benchmark protocols have not yet been performed using `fio`. The key component to consider how fast your write speeds are. NVMe SSDs are a must for using these cameras and the sequential write speed must be rated above the max data rate you calculate.
