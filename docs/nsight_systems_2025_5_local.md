# User-local Nsight Systems 2025.5

The system CUDA 12.2 installation provides Nsight Systems 2023.2.3. Profiling
the CUDA 13.1 NVENC array probe with that version reported
`Unknown driver API function index: 780` and then produced a truncated trace.
The isolated profiler below avoids changing the system CUDA installation.

## Pinned source

- CUDA redistribution manifest: `redistrib_13.1.1.json`
- Manifest SHA-256:
  `97cf605ccc4751825b1865f4af571c9b50dd29ffd13e9a38b296a9ecb1f0d422`
- Nsight Systems version: `2025.5.2.266`
- Official archive:
  `nsight_systems/linux-x86_64/nsight_systems-linux-x86_64-2025.5.2.266-archive.tar.xz`
- Archive size: `1098114048` bytes
- Archive SHA-256:
  `76113b3f75ba620d11242a5b720c9eddd168be41ce879a5fb9d4f198181e19a4`

The complete source URLs and the same pins are recorded in
`stage_nsight_systems.py` and in
`pancakebatter-nsight-systems-source.json` inside the staged prefix.

## Recreate the prefix

Run as the normal user from the Pancakebatter checkout:

```bash
python3 stage_nsight_systems.py \
  --manifest-cache /tmp/cuda-redistrib-13.1.1.json \
  --archive-cache /tmp/nsight_systems-linux-x86_64-2025.5.2.266-archive.tar.xz \
  --prefix ~/.local/opt/nsight-systems-2025.5.2-nvenc
```

The prefix must be new. The stager verifies the manifest, archive size and
hash, every archive member and link target, and every final symlink. It does
not invoke `sudo`, a package manager, `ldconfig`, alternatives, or update
`PATH`.

## Commands

Use the binaries by absolute path:

```text
~/.local/opt/nsight-systems-2025.5.2-nvenc/bin/nsys
~/.local/opt/nsight-systems-2025.5.2-nvenc/host-linux-x64/QdstrmImporter
```

The importer converts a raw stream explicitly:

```bash
~/.local/opt/nsight-systems-2025.5.2-nvenc/host-linux-x64/QdstrmImporter \
  --input-file capture.qdstrm \
  --output-file capture.nsys-rep \
  --force-overwrite
```

Keep the profiler version and its bundled importer together. Do not feed a
2025.5 stream to the older system importer.
