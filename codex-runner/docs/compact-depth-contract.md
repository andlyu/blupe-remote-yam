# Private compact paired RGB-D, version 1

Source: BluPe YAM local camera transport; defined October 1, 2026.
This optional private representation is `rgbd-compact-npz-v1`. It preserves
registration, original dimensions, capture timestamps, calibration identity and
every float32 depth bit. The paired color is JPEG quality 90, with no resizing,
cropping or rectification. JPEG changes color samples; depth is lossless.

The NPZ has exactly `rgb_jpeg.npy`, `depth_bytes.npy`, and `metadata.npy`:

| Array | Type/shape | Contract |
| --- | --- | --- |
| rgb_jpeg | uint8, one dimension, at most 4 MB | Complete JPEG; decoded dimensions match calibration |
| depth_bytes | uint8, (4,H,W) | Byte planes of little-endian float32 optical Z, in meters |
| metadata | scalar Unicode JSON | Original source metadata plus transport fields below |

Depth reconstruction uses `depth_bytes.transpose(1,2,0).copy().reshape(-1).view('<f4').reshape(H,W)`.
Metadata adds `transport_format: "rgbd-compact-npz-v1"`, `rgb_encoding: "jpeg"`,
and `depth_dtype: "<f4"`. All source timestamps, robot/camera/calibration ID,
units, alignment, optical-Z semantics and sensor metadata stay unchanged.

The exporter reads the existing loopback camera API; it opens no camera or motor
device and has no controller access. It rejects source frames older than five
seconds. Consumers permit that age only with confirmed stopped feedback;
otherwise depth must be less than two seconds old after transfer and decode. They
retain calibration quality warnings, and never substitute a saved frame.
Missing or expired depth returns `204 No Content` with an empty body and
`X-Camera-Error: no_image`. Remote-yam reports **No image** and retries the
complete observation while the run remains active and the robot is stopped.

This private contract is accepted only when an explicit `--depth-url` is supplied.
The public API's default `rgbd-npz-v1` format and behavior remain unchanged.

## Millimeter variant

`rgbd-mm-npz-v1` uses the public depth PNG's existing millimeter precision to
reduce transfer size further. Its arrays are `rgb_jpeg`, `depth_mm` (little-endian
uint16 H×W), and `metadata`. Zero means missing or outside the representable
range. Encoding rounds finite source depth from 0.0005 through 65.535 meters to nearest millimeter.
The maximum rounding error is 0.0005 meters; original float bits are not retained
by this variant. RGB registration and capture timestamps remain unchanged.

Metadata sets `transport_format: "rgbd-mm-npz-v1"`, `rgb_encoding: "jpeg"`,
`depth_dtype: "<u2"`, `wire_depth_units: "millimeters"`,
`depth_quantization_m: 0.001`, and `depth_max_error_m: 0.0005`.
The source's `depth_units: "meters"` describes reconstructed optical Z;
clients explicitly convert the uint16 wire array to float32 meters. Measurement
responses retain the rounding bounds, and patch-spread validation adds the
one-millimeter quantization interval conservatively. Identity, calibration,
resource limits and the same freshness checks apply.

## Local wrist RGB snapshots

The same loopback exporter can serve `GET /cameras/left.jpg` and
`GET /cameras/right.jpg` from the existing relay's snapshot API. The fixed YAM
mapping is left device10, USB descriptor402323071768; right device4, USB descriptor402323071268.
The exporter checks relay identity and source freshness. It converts the relay's
`X-Capture-Monotonic` to Unix capture time using the robot's own clocks, using the source capture timestamp; receipt time is never substituted.

JPEG responses carry `X-Captured-At`, `X-Robot-Id: yam-1`, `X-Camera-Role`, and
`X-Camera-Serial`. An explicitly configured `--rgb-origin` consumer checks these
headers, calibrated image dimensions, bounded complete JPEG data, and capture
age at receipt. Each policy capture uses both fresh wrist views plus overhead
RGB from the paired depth bundle. No top JPEG download is needed. Wrist views
are independent captures, not synchronized to the depth frameset. Calibration
currently has null serial fields; fixed role identity is checked against the
verified relay mapping, while its provisional extrinsics warnings remain.
## All-camera private RGB-D and freshness (October 1)

The explicit private origin also exposes fixed `/cameras/top.rgbd.npz`,
`/cameras/left.rgbd.npz`, and `/cameras/right.rgbd.npz` routes. The existing
`/16/rgbd.npz` remains an alias for top. They read the existing single-owner
relay, preserving source timestamps, role, SDK serial, calibration ID, optical
Z semantics and color registration. Calibrated/public roles are left=device10
(SDK323622273130), right=device4 (SDK353322271772), top=device16
(SDK244622072159). USB descriptor serials differ from SDK serials.

Private RGB-D responses include `X-Source-Capture-Age-S`: nonnegative source
age measured on the robot's monotonic clock after encoding. The client adds
its full monotonic request/response duration, plus subsequent local processing,
to obtain a conservative upper bound on capture age. Require that bound to be
at most five seconds when the robot is confirmed stopped, and less than two
seconds otherwise. This works without synchronized wall clocks: the server's
age sample is between the client's request start and response completion.
Preserve the original finite positive `captured_at`; require it to match
`X-Captured-At`. Missing/invalid ages, identity, serial or calibration headers
fail closed. Public/raw APIs retain their original wall-clock freshness check.

Each camera's color/depth pair is atomic. Different cameras are not hardware
synchronized. Require all three pairs to remain fresh at observation assembly.
Wrist-to-base poses require measured settled joints from the current observation
and the calibrated mounting transform; never reuse a wrist pose after motion.
Calibration quality remains provisional.
