# Visualization and filtering

This package renders radar observations and inspects recorded calibration images.

- `filter_schema.py` defines the shared filter fields, defaults, and value
  normalization used by the interface and radar worker.
- `graph_filter.py` applies range, quality, state, and classification filters
  to cluster and object observations.
- `graph_draw.py` renders the accepted points and radar context with OpenCV.
- `calibration_data.py` loads calibration journals, compares decoders and image
  variants, selects a candidate offset, and maps source regions between original
  and undistorted coordinates.
- `calibration_viewer.py` provides a separate Tk window, started from the GUI's
  Visualization tab or the root `visualize_calibration_recording.py` program.
- `calibration_worker.py` owns background decoding, navigation requests, and
  cancellable folder scans independently of the window widgets.

The package does not acquire sensor data or save recordings. Those concerns
remain in `sensors/radar/` and `processing/recording/` respectively.

## Calibration viewer

```bash
python3 visualize_calibration_recording.py /path/to/calibration-recording \
  --intrinsics /path/to/intrinsic_coefficients.json --undistorted
```

Without arguments the program opens a folder chooser interface. On this checkout
it suggests the provided sibling `Segcom Sincronização GERAL/Extrinsic` intrinsic
file if present. A recording-local `intrinsic_coefficients.json` takes precedence
for standalone launches. Any suggestion can be replaced or cleared.

The image mode and decoding mode are independent: changing to Undistorted can
show the original decoder's regions mapped onto corrected pixels; checking
**Also decode undistorted** actually runs the decoders on corrected pixels too.
The table labels every observation's method and source image. Its quadrant comes
from the matched display journal, never from the camera image midpoint. Boxes
are OpenCV detection regions, hulls of ZBar scan locations, or registered
scanline regions; the details retain their source-coordinate bounds. Clicking a
row changes the inspected region without changing which candidate the algorithm
selected. **Show all codes** overlays one region per distinct code.

OpenCV and the optional system `libzbar` decode independently. EAN check digits,
unique journal lookup, ZBar scanline support, and host-receipt causality checks
are retained. Rejected readings stay visible with their reason. The outline
method retains its existing direct/predecessor inference and timing checks.
No production decoder depends on the generated `recordings/` analysis scripts.

The candidate uses the host-anchored PTS time minus the selected screen time.
It prefers consistent outline evidence; other readings use the newest decoded
code and display **Provisional**. Markers observed across incompatible display
states are flagged. Wide/conflicting generations prevent a single prediction;
suspect arrival/replacement timing prevents inclusion in the folder estimate.
The encoded time, exact journal time, PTS, host media/receipt times, DVR NTP,
and recorded exposure correction remain distinct. Missing timing stays missing.
Neither decoder agreement nor outline support proves the physical exposure time.

The worker serializes decoder access and prioritizes navigation between folder
scan steps. Its image-result cache is bounded; it does not retain every decoded
image in memory. Cancelled scans are labelled partial, and separate stream
epochs never share an aggregate offset. Loading another folder or intrinsic
matrix invalidates cached results and the folder estimate. The GUI launcher
does not stop live acquisition; closing the main GUI also closes its viewer.

The originals, journals, camera settings, and intrinsic file are read-only.
Undistortion uses the supplied lens model and a specified centered output matrix
at 75% focal scale, retaining the input dimensions. Optional `image_size` in the
intrinsic JSON identifies the calibration resolution. Without it the recording's
resolution is assumed and the interface says so. Incorrect camera/lens or size
metadata cannot be corrected by the viewer.

Tests exercise timing choices, missing/invalid inputs, coordinate round-trips,
partial scans, launch arguments, and recovery using recorded frames without
opening any windows. Actual layout and interaction require the user's visual
check; no screen screenshots are taken automatically.
