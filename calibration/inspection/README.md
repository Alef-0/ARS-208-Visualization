# Calibration recording inspection

`viewer.py` owns the window, `worker.py` serializes decoding and scans,
`data.py` joins image observations with timing, and `region_picker.py` records
the four manually marked panels. Shared lens geometry lives in `../decoding/`.

```bash
python3 visualize_calibration_recording.py /path/to/calibration-recording \
  --intrinsics /path/to/intrinsic_coefficients.json --undistorted
```

Without arguments the program opens a folder chooser interface. On this checkout
it suggests the provided sibling `Segcom Sincronização GERAL/Extrinsic` intrinsic
file if present. A recording-local `intrinsic_coefficients.json` takes precedence
for standalone launches. Any suggestion can be replaced or cleared.

With saved manual panels, decoding uses their undistorted regions regardless of
the displayed image mode. Their overlays are mapped to Original or Undistorted
as selected. Before marking panels, automatic inspection can decode the original
image and add corrected-image reads through **Also decode undistorted**.
The table labels every observation's method and source image. Its quadrant comes
from the matched display journal, never from the camera image midpoint. Boxes
are OpenCV detection regions, learned interior bands, or registered panel
regions; details retain their preprocessing and source-coordinate bounds. Clicking a
row changes the inspected region without changing which candidate the algorithm
selected. **Show all codes** overlays one region per distinct code.

OpenCV is the only decoder. **Local contrast** combines grayscale and CLAHE
reads; **Binary retry** optionally adds local Otsu on regional bands. Before
manual marking, regions are learned from journal-matched codes for each image variant.
Their median locations persist across sequential frames, with oversized merged
boxes rejected, confirmed movement/seeking resetting the model, and stale
regions expiring after 60 analyzed frames. EAN check digits, exact journal
lookup and host-receipt causality are required. Rejected readings retain reasons.
The new underline is checked separately from decoding. Old borders are ignored
as current-marker evidence in this viewer; their reads stay provisional.

The candidate uses the host-anchored PTS time minus the selected screen time.
Before a folder scan, follow [manual selection](#manual-selection-and-saved-geometry)
to replace automatic discovery with recording-specific fixed panels.
Selection prefers consistent underline evidence; other readings use the newest decoded
code and display **Provisional**. Markers observed across incompatible display
states are flagged. Wide/conflicting generations prevent a single prediction;
suspect arrival/replacement timing prevents inclusion in the folder estimate.
The encoded time, exact journal time, PTS, host media/receipt times, DVR NTP,
and recorded exposure correction remain distinct. Missing timing stays missing.
Neither image-variant agreement nor underline support proves physical exposure time.

The worker serializes decoder access and prioritizes navigation between folder
scan steps. Its image-result cache is bounded; it does not retain every decoded
image in memory. Cancelled scans are labelled partial, and separate stream
epochs never share an aggregate offset. Loading another folder or intrinsic
matrix invalidates cached results and the folder estimate. The GUI launcher
does not stop live acquisition; closing the main GUI also closes its viewer.

The originals, journals, camera settings, and intrinsic file are read-only.
Undistortion uses the supplied lens model and OpenCV's optimal output matrix,
retaining the input dimensions. **Undistortion alpha** defaults to 0 and offers
0.25, 0.5, 0.75 and 1. The same matrix maps source overlays; changing alpha or
preprocessing reloads the model and invalidates folder estimates. Optional `image_size` in the
intrinsic JSON identifies the calibration resolution. Without it the recording's
resolution is assumed and the interface says so. Incorrect camera/lens or size
metadata cannot be corrected by the viewer.

Tests exercise timing choices, missing/invalid inputs, coordinate round-trips,
partial scans, launch arguments, and recovery using recorded frames without
opening any windows. Actual layout and interaction require the user's visual
check; no screen screenshots are taken automatically.

## Manual selection and saved geometry

**Analyze folder** now asks you to mark panels if no saved selection exists.
Use **Mark 4 panels…** to replace a selection. The standalone regular analyzer
does the same; `--mark-panels` explicitly starts a new selection, and
`--automatic` explicitly requests the earlier automatic registration instead.

The picker starts with a random image from the middle 40–60% of the recording,
undistorted with the chosen camera coefficients. Click four corners of each
white barcode panel, including quiet margins but excluding time/underline:
panels clockwise from top-left, and each panel's corners TL, TR, BR, BL.
Mouse-wheel zoom, scrollbars and Undo allow precise placement. You can change
frames while retaining points when a panel is blank. Brightening is only a
marking aid. Alpha can be changed before marking; clear the points to change
it afterward. Save is enabled after all 16 corners have been selected.

`calibration_regions.json` records undistorted coordinates, image dimensions,
alpha, the output camera matrix, and hashes of camera/display journals,
intrinsics and the images used for marking. Selections cannot silently transfer
between recordings or changed coefficients. A different viewing alpha maps
the stored points through the two output camera matrices; it does not reinterpret
old coordinates in a new geometry.

With marked panels, the viewer and regular analyzer decode only those four
regions with OpenCV and local contrast. Each panel has its own perspective
mapping, so a weakly detected or glaring top panel does not depend on automatic
box discovery. Underlines and expected-empty areas are sampled separately
using the local panel geometry. Out-of-image underlines and undistortion padding
are unverified; marking a barcode does not establish that it is current.
Transition, causality and display-replacement timing exclusions still apply.

```bash
python3 -m calibration.inspection.region_picker \
  recordings/new_calibration recordings/newer_calibration

python3 analyze_calibration_recording_offset.py recordings/newer_calibration \
  --output-dir recordings/newer_calibration_analysis
```

The command-line analyzer accepts `--alpha .25` to change the output projection
while mapping the saved points consistently. It records the analysis alpha
separately from the marking alpha and leaves the saved selection unchanged.
Use a separate output directory to compare alpha settings.

The fixed regions assume the camera and monitor remain stationary. Mark each
recording separately; if the camera moved within one recording, its affected
frames need review rather than trusting a single geometry.
