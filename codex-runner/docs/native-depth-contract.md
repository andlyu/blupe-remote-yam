# Native RGB-D transport, version 1

`rgbd-jpeg-z16-png-v1` uses `.rgbd-native.zip`. Clients select the same-origin
camera path advertised by the public calibration report's `native_format` and
`native_bundle_url`. Legacy `.rgbd.npz` remains supported. Source frame age,
sensor identity, calibration binding and policy freshness limits are unchanged.

The archive has exactly four ZIP_STORED members:

| Member | Encoding |
| --- | --- |
| `rgb.jpg` | JPEG quality 90; original color frame dimensions, no overlays or resampling |
| `depth.png` | Original aligned sensor Z16, grayscale uint16 PNG, compression level 1 |
| `metadata.json` | UTF-8 source metadata plus the versioned transport fields |
| `depth-patches.bin` | Zero or more little-endian uint32 pairs: flat pixel index, original float32 bits |

Color is lossy. Depth is bit-exact relative to the capture's existing optical-Z
float32 image. `depth_scale_m` is the original sensor scale, including wrist
scales near 0.1 mm. The integer PNG is **not** the public millimeter preview.

Reconstruct color-optical depth using the factory color rays, recorded
`T_color_depth`, and original scale. The algorithm follows librealsense 2.58.1
`rs.cpp` deprojection and `align.cpp`'s retention of source Z. See the vendored
references in the integration repository's `docs/refs/realsense-depth/`.
`rgbd_native.py` specifies canonical separate NumPy float32 operations, without
BLAS or fused multiply-add. Compiler rounding differences from SDK capture are
represented by ordered, unique, in-range patches. Apply those patches, require
finite nonnegative depth, preserve missing-depth zeroes, then verify SHA-256
over C-order little-endian float32 bytes against `depth_sha256`. A mismatch is
an invalid frame; never bypass it or substitute rounded millimeters.

The decoder bounds dimensions to 2 million pixels, the archive to 16 MB,
metadata to 64 KiB, JPEG and PNG members to 4 MB each, and patches to 8 bytes
per calibrated pixel. It checks PNG dimensions/type before image allocation
and validates identity and factory profile against the public calibration.

When the publisher supplies this format, the API can produce legacy NPZ arrays
on demand. These contain **JPEG-derived RGB** and exact original metric depth;
metadata declares `rgb_encoding=jpeg` and `source_transport_format`. Clients
requiring original lossless color must not treat those arrays as raw sensor RGB.
The existing display JPEG stream is independent.
