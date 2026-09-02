# Segcom Sensors GUI

Segcom Sensors GUI is a desktop tool for operating a three-group Continental
ARS40X radar setup together with cameras and the GPS exposed by a network DVR.
It provides live monitoring, radar configuration, synchronized radar/camera
recording, manual paired snapshots, playback, and a camera-delay calibration
workflow.

This README describes the current working tree. The hardware addresses,
credentials, timing values, and physical group layout are deployment-specific
and should be confirmed on the real Segcom installation.

## Main capabilities

- Connect to a TCP gateway that forwards CAN packets from radar groups A, B,
  and C.
- Decode radar configuration, cluster, quality, object, extended-object, and
  collision-warning messages.
- Send runtime or non-volatile configuration changes to one or all radars.
- Display the selected radar group as a filtered top-down point plot.
- Open the corresponding DVR camera stream over RTSP, with automatic decoder
  selection for desktop NVIDIA, Jetson, or CPU decoding.
- Record PCD radar frames and JPEG camera frames into synchronized recording
  folders.
- Capture a single camera/radar pair into a snapshot folder.
- Play complete recordings at their recorded cadence, or inspect paired
  snapshots one frame at a time.
- Poll the DVR GPS endpoint and open the last position in Google Maps.
- Record camera channel 4 while displaying monotonic EAN-13 markers for
  latency and clock-drift analysis.
- Convert recorded PCD trees to CSV while preserving images and metadata.

## Deployment assumptions

The current source contains fixed addresses and credentials:

| Device or service | Current endpoint | Used by |
| --- | --- | --- |
| Radar CAN gateway | `192.168.1.101:2323` over TCP | `sensors/radar/connection_communication.py` |
| DVR RTSP cameras | `192.168.1.108:554`, channels 1-4 | `sensors/camera/camera_gstreamer.py` |
| DVR GPS status | `http://192.168.1.108/cgi-bin/positionManager.cgi?action=getStatus` | `sensors/gps/gps_connection.py` |

The DVR username and password are also embedded in the camera and GPS source.
Do not publish a deployment copy of this repository without first deciding how
those credentials should be handled.

The source treats channels 1, 2, and 3 as groups A, B, and C respectively.
The user interface labels those positions LEFT A, MIDDLE B, and RIGHT C.
Camera channel 4 is reserved for calibration.

## Running the application

Use Python 3 from the repository root:

```bash
python3 main.py
```

Python packages are listed in `requirements.txt`. The host also needs the
native GStreamer runtime and plugins required by the selected H.264 decoder,
plus a working display backend for FreeSimpleGUI, OpenCV, and Pygame.

The optional `SEGCOM_CAMERA_DECODER` environment variable accepts `auto`,
`rtx`, `orin`, or `cpu`. Automatic selection prefers Jetson decoding on a
Jetson, desktop NVIDIA decoding elsewhere, and keeps the CPU decoder as the
fallback.

## Runtime architecture

`main.py` owns the FreeSimpleGUI window and starts workers with Python's
`spawn` multiprocessing context:

```text
FreeSimpleGUI process
├── radar worker: TCP/CAN input, decoding, plot, PCD recording
├── camera worker: RTSP/GStreamer input, display, JPEG recording
├── GPS worker: DVR position polling and map link
├── recording playback worker
└── snapshot playback worker
```

The GUI sends commands to each worker through a dedicated pipe. Workers return
state, progress, warnings, and errors through one bounded status queue. A
shared shutdown event coordinates normal termination. The calibration display
is started only when requested and runs in its own process.

`application_core.py` contains the common event loop and record/playback orchestration.
`main.py` extends that behavior with calibration and snapshot-playback modes.
Likewise, `interface_core.py` contains the common window and state logic while
`menu_configurations.py` adds the newer controls.

## Live radar flow

The radar worker opens a non-blocking TCP connection to the CAN gateway. Each
gateway packet is 23 bytes and contains the CAN ID, eight data bytes, a source
timestamp, and a channel number.

The code recognizes these main ARS40X messages:

- `0x200`: configuration command sent to the radar.
- `0x201`: configuration/state response shown in the GUI.
- `0x600`, `0x60A`: start markers for cluster and object frames.
- `0x701`, `0x702`: cluster general and quality data.
- `0x60B` through `0x60E`: object general, quality, extended, and warning data.

A new `0x600` or `0x60A` closes the preceding logical frame. The completed
frame can then be plotted, retained briefly for manual snapshot matching, and
queued for recording. Plot filters include distance, RCS, dynamic property,
false-alarm probability, ambiguity state, and invalid-state flags.

## Live camera and timestamp flow

The camera worker builds one GStreamer pipeline with a tee:

- The display branch is intentionally leaky and holds only the newest frame so
  the live view does not accumulate delay.
- The full-resolution capture branch uses a bounded 30-buffer pipeline queue
  and a separate bounded image-writer queue.

The RTSP source currently allows TCP or UDP negotiation, requests RTCP and
reference timestamp metadata when supported, and defaults to 145 ms of
GStreamer jitter-buffer latency. That 145 ms value controls buffering; it is
not itself a measured end-to-end correction.

Saved-frame time starts from buffer PTS mapped through the GStreamer segment
and pipeline clock to a stable host-time anchor. Valid per-frame camera NTP
metadata disciplines that PTS timeline gradually. Missing NTP falls back to
PTS, large NTP steps require repeated confirmation, and invalid or non-forward
PTS frames are rejected and counted.

The separate camera latency adjustment defaults to 109 ms. It is subtracted
when associating a camera observation with radar time and is recorded in
metadata. It does not replace or configure the RTSP jitter buffer. This value
is a calibration result, so it should be rechecked after changes to the DVR,
stream session, decoder, network path, or capture setup.

See `sensors/camera/README.md` for the pipeline and timestamp policy in more detail.

## Recording and snapshots

Starting a recording creates one folder per selected radar group:

```text
recording_A_YYYYMMDD_HHMMSS_microseconds/
├── frame_000001.pcd
├── camera_000001.jpg
├── recording.json
└── timestamps.json
```

Radar frames are written as PCD point clouds. Camera frames are written as
JPEGs into every selected group folder. `recording.json` is the authoritative
ordered association between radar frames and camera frames; `timestamps.json`
keeps the older filename-to-radar-time mapping.

For each camera frame, the recorder subtracts the configured camera delay and
matches the result to the closest still-unpaired radar frame. The metadata
retains the camera time, applied delay, and residual synchronization error.

A manual snapshot first captures a valid-timestamp camera frame for the chosen
group. The radar worker then selects the closest completed radar frame from a
three-second history. The pair is rejected if the residual difference exceeds
500 ms. Successful snapshots use the same PCD/JPEG and JSON contracts as a
normal recording, so they can be played and converted by the same tools.

See `processing/recording/README.md` for file schemas and overload handling.

## Operating modes

Live monitoring, normal playback, snapshot playback, and calibration camera
mode are coordinated as mutually exclusive uses of the camera/radar displays.
When playback or calibration needs the devices, the GUI first stops active
recording and closes conflicting live workers before starting the requested
mode.

Normal playback follows recorded timestamps and supports restart and five
second seeks. Snapshot playback can restrict the list to entries that contain
both image and PCD data, pause, step backward or forward, and save the current
pair into another snapshot folder.

## Calibration

The existing calibration workflow uses camera channel 4 and the fullscreen
clock in `CALIBRATION/calibration_screen_clock.py`. One persistent fullscreen
canvas retains a configurable 1-4 clockwise-updated EAN-13 timestamps. The
default is 3: the quadrant immediately ahead remains blank to expose partial
updates/camera artifacts. Expired quadrants are cleared, and a pure-white
rectangular outline identifies the newest marker. Choose the count in the
Calibration tab's **Visible barcodes** control before starting. The display
runs on one thread, samples monotonic time immediately
before drawing near the next deadline, and presents once with `flip()`.
Press **P** with the calibration window focused to freeze the display for a
screenshot; press it again to resume with a fresh timing schedule. Camera
recording continues. Held markers and the first resumed marker are excluded
from automatic timing measurements; Q/Escape still closes the display.

When camera 4 is open, starting the clock display schedules a calibration
recording after three seconds. Its destination is created immediately so the
display timing journal includes the warm-up. Closing the display stops camera
recording but leaves camera 4 open. In
addition to JPEGs, the calibration folder contains a compact append-only frame
journal, session clock anchors, sparse RTCP/transport events, and a recording
summary. `display_timestamps.jsonl` additionally stores screen geometry,
encoded times, deadlines, pre-flip submission times, flip-return observations,
and irregular/missed-period candidates. Camera frame time is host-anchored PTS; NTP is retained independently
for comparison and does not modify that timestamp. The configured 109 ms
camera-to-radar adjustment remains provisional until a session-specific
calibration validates it.

After recording, analyze the EAN-13 markers and timing series with:

```bash
python3 analyze_calibration_recording_offset.py /path/to/calibration-recording
```

The analyzer registers the white outline to the display geometry and verifies
EAN guards, parity and checksum on multiple scanlines. It uses the outlined
marker directly, or the immediate predecessor plus one measured display
period when the newest code cannot be read. It rejects ambiguous outlines,
conflicting scanlines, unexpected content in the blank quadrant, and unstable display timing. Direct and inferred results
are reported separately, with excluded observations retained in the JSON.
Historical recordings without a display journal retain the legacy decoder.

The report compares screen monotonic time with host-anchored PTS, NTP
progression, the DVR-to-host clock offset, receipt delay, and host clock
movement. The marker now represents the time sampled before drawing, not
`render time + 16.667 ms`. Neither it nor flip-return time measures physical
panel scanout: submission-to-light delay, millisecond payload quantization,
screen raster and camera exposure remain calibration uncertainty. Pygame vsync
is a request, not a hardware guarantee. See `CALIBRATION/README.md` for timing
details, standalone usage and manual screen-plane registration.

## CSV conversion

Convert a recording tree with:

```bash
python3 convert_to_csv.py /path/to/recordings
```

The tool creates a sibling folder named `<source> - CSV`, converts every PCD
file to CSV, copies images, updates `.pcd` references in Segcom metadata, and
writes a value dictionary beside converted point clouds. It refuses to replace
an existing output folder and removes a partial output tree if conversion
fails.

## Tests

The tests use `pytest` and avoid real hardware by replacing the DVR, CAN
gateway, GStreamer samples, filesystem writers, and GUI objects with focused
fakes. In this environment, disable unrelated globally installed pytest
plugins:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

Unit tests provide static and simulated evidence only. Camera optics,
fullscreen behavior, decoder availability, network timing, radar traffic, and
the complete GUI workflow still require checks on the actual installation.

See `tests/README.md` for the test-area map.

## Source map

| Path | Responsibility |
| --- | --- |
| `main.py` | Current application entry point and mode orchestration |
| `application_core.py` | Shared GUI event, recording, playback, and shutdown behavior |
| `menu_configurations.py` | Current window layout and UI state extensions |
| `interface_core.py` | Shared GUI layout and state transitions |
| `sensors/` | Radar, RTSP camera, timestamp, and GPS integrations |
| `processing/` | Plotting, filtering, recording, PCD reading, snapshots, and playback |
| `CALIBRATION/` | Existing camera timing calibration display |
| `convert_to_csv.py` | Recursive PCD-to-CSV export |
| `content/` | ARS40X technical-documentation extracts |
| `recordings/` | Generated recording data, kept outside source packages |
| `snapshots/` | Generated or manually assembled snapshot data |
| `tests/` | Automated tests, kept outside source packages |

## Points to confirm with the project owner

The code supports the following interpretation, but these product-level facts
are not independently proven by source alone:

- LEFT/MIDDLE/RIGHT and A/B/C are the intended physical channel assignments.
- The gateway packet timestamp is intentionally ignored in favor of host
  receipt time for radar frame recording.
- Camera channel 4 is always the calibration camera.
- The current 109 ms camera adjustment is the intended operational default for
  this deployment.
- Snapshot matching should continue to allow up to 500 ms residual error.
- Recording camera frames into every selected radar folder is the desired data
  duplication model.
