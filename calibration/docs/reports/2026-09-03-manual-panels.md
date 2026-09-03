# Manual panel analysis — 3 September 2026

Manual marking solves most of the barcode-location problem in these recordings.
It does not yet produce a defensible camera timing correction: both complete
recordings have zero frames accepted by the current-marker and display-timing
checks. Decoding more panels exposes temporal overlap that automatic detection
previously missed.

## What changed and what ran

The picker opens a random frame from the middle 40–60% of each recording,
undistorted. The user selected all 16 corners separately for each recording.
The saved references are `camera_000991.jpg` for `new_calibration` and
`camera_000464.jpg` for `newer_calibration`, both marked at alpha 0.
Selections are bound to the recording journals, reference images and intrinsic
coefficients by hashes.

The viewer and regular analyzer now decode three interior bands in each marked
panel using OpenCV, with grayscale and local contrast enhancement. They do not
need automatic panel detection. The time and underline remain outside barcode
decoding. Underlines and the expected blank area are checked separately;
missing image coverage and black undistortion padding cannot prove an underline
absent. Different generations remain conflicts rather than majority votes.

Both complete recordings were analyzed at the marking alpha 0 and again at
alpha 0.25. A paired sample, every 50th frame, compared automatic detection,
manual panels, grayscale, local contrast, binary thresholding, and alphas
0, 0.25, 0.5, 0.75 and 1. Sample counts are 38 and 22 respectively. This regular
sampling can favor a particular display/camera phase; the complete analyses
provide the stronger coverage evidence. No live camera correction was changed.

## Complete-recording coverage at alpha 0.25

The following counts require a matching display-journal code in at least two
distinct bands. Repeating a band through different contrast settings does not
provide another independent band. Percentages use all saved frames as their
denominator, including frames where the panel could legitimately be blank.

| Measurement | new_calibration | newer_calibration |
|---|---:|---:|
| Saved frames | 1,894 | 1,081 |
| At least one supported barcode | 1,894 / 1,894 | 1,081 / 1,081 |
| Top-left | 1,894 (100%) | 1,080 (99.91%) |
| Top-right | 1,537 (81.15%) | 1,081 (100%) |
| Bottom-right | 1,894 (100%) | 1,081 (100%) |
| Bottom-left | 1,894 (100%) | 1,081 (100%) |
| All four corners supported | 1,537 (81.15%) | 1,080 (99.91%) |
| Accepted timing measurements | **0** | **0** |

With just one matching band, the top-right panel in `new_calibration` is readable
in 1,872 frames (98.84%). The stronger two-band requirement explains the lower
timing-quality coverage. Its two-band coverage across chronological thirds is
476/631, 525/631 and 536/632; the fixed middle-frame geometry remains useful
throughout. In `newer_calibration`, all four panels have two-band support in
360/360, 360/360 and 360/361 frames. Its only missing top-left result is the
final saved frame.

At alpha 0, two-band top-right coverage in `new_calibration` was 1,481/1,894;
alpha 0.25 improves that to 1,537. In `newer_calibration`, alpha 0 crops part of
the predicted bottom-right underline: its mapped vertical extent reaches
about y=1081 on a 1080-row image. Alpha 0.25 places the indicator and its local
comparison pixels inside valid image coverage, without asking for new corners.
This removes the geometry rejection, but not the temporal ambiguity.

## Automatic detection, contrast and alpha

These paired-sample counts require at least one journal-matched band, not the
two-band support used above. A frame-code pair counts a code once per frame.

| Method | new: pairs / top-left / top-right (38 frames) | newer: pairs / top-left / top-right (22 frames) |
|---|---:|---:|
| Original image, automatic discovery plus learned regions | 76 / 0 / 0 | 56 / 0 / 11 |
| Undistorted alpha 0, automatic discovery plus learned regions | 0 / 0 / 0 | 38 / 0 / 16 |
| Manual alpha 0, grayscale plus contrast | 147 / 38 / 33 | 88 / 22 / 22 |
| Manual alpha 0.25, grayscale plus contrast | 151 / 38 / 37 | 88 / 22 / 22 |

The automatic method did not read the top-left panel in either sample. Supplying
its location recovered it in every sampled frame. That supports a location
failure; these results alone do not establish glare as the cause.

For manual alpha 0.25 in `new_calibration`, grayscale alone gave 137 pairs and
local contrast gave 151. In `newer_calibration`, both gave 88. Whole-image Otsu
gave zero pairs in either sample. Local Otsu gave 66 and 86 pairs at alpha 0.25,
adding no values beyond grayscale plus contrast there. At alpha 0 it did add
one pair to `new_calibration`, and at alpha 0.5 it added five: binary filtering
is retained as an optional retry, not used as the default representation.

Manual combined pairs for alphas 0 / 0.25 / 0.5 / 0.75 / 1 were
147 / 151 / 134 / 144 / 114 in `new_calibration` and
88 / 88 / 88 / 89 / 88 in `newer_calibration`. The 89 includes a conflicting
second generation in one panel, not extra reliable coverage. Alpha 0.25 is a
useful setting for these recordings, not a universal optimum. The supplied
intrinsic file omits calibration image size, so its applicability to these
1920×1080 images remains an assumption.

## Why no timing measurements were accepted

Both display journals specify **three** visible panels. Nevertheless, the
1,537 and 1,080 frames with four supported codes contain display generations
that cannot belong to one logged three-panel state. The display code clears
the expired quadrant before its single presentation call. The saved images
therefore provide evidence of overlap between display states somewhere in
display presentation, camera exposure, or camera/video processing; these data
do not isolate which stage is responsible.

The remaining frames still fail indicator, blank-area or timing checks.
Underlines are located by extrapolating the marked panel geometry, so thin-line
detection and approximate intrinsic/point geometry also limit their use.

| First reported rejection, alpha 0.25 | new | newer |
|---|---:|---:|
| Missing or multiple current indicators | 908 | 478 |
| Mixed display generations | 603 | 498 |
| Expected blank quadrant still has content | 273 | 0 |
| Code disagrees with panel location or display journal | 45 | 103 |
| Selected display update has unstable timing | 46 | 0 |
| Following replacement has unstable timing | 18 | 0 |
| Following replacement cannot be verified | 1 | 1 |
| Different valid values within a panel | 0 | 1 |

These are mutually exclusive first-failure counts, not totals of every issue.
For example, two underlines were detected in 919 `new_calibration` frames,
including frames rejected earlier for a code mismatch. In `newer_calibration`,
540 frames have no detected underline and 541 have one. A single detected
underline cannot resolve the incompatible four-code history. The final marker
also lacks a following replacement observation and remains unverified.

For diagnostics only, PTS minus the newest supported decoded payload has median
77.34 ms (5th–95th percentiles 62.26–97.09 ms) in `new_calibration`, versus
89.46 ms (77.19–102.95 ms) in `newer_calibration`. Receipt minus that payload has
medians 216.61 and 228.50 ms. **These are not validated exposure offsets.**
Dropping overlapping reads changes the first recording's diagnostic median to
90.28 ms, illustrating selection bias; the retained group still has no accepted
current-marker measurements. There is no accepted sample on which to fit a
correction or evaluate a timing holdout. The configured 109 ms correction remains
unchanged.

## Recording and display timing

| Measurement | new | newer |
|---|---:|---:|
| PTS span | 63.101 s | 36.002 s |
| Average PTS frame rate | 29.9994 FPS | 29.9985 FPS |
| Median receipt minus PTS | 138.53 ms | 138.32 ms |
| Receipt minus PTS, 5th–95th percentiles | 123.30–158.08 ms | 123.80–157.69 ms |
| Median camera NTP minus host media timestamp | +27.722 s | +27.709 s |
| Recorder queue drops / rejected invalid timestamps | 0 / 0 | 0 / 0 |
| Reported lost RTP packets | 0 | 16 |
| Display missed-period candidates | 257 | 12 |
| Display irregular intervals | 128 | 6 |
| Display late submissions | 50 | 52 |

PTS steps alternate around 20 and 40 ms, with occasional 50 ms steps. Their
mean is about 33.33 ms; the roughly 40 ms median does not mean the recording
runs at 25 FPS. All frames observed by the recorder were saved. Sixteen lost
RTP packets do not establish sixteen lost camera frames. Neither recording has
an unusual-PTS-gap candidate in its recorder summary.

The newer session has much better logged display pacing, but its optical
ambiguity remains. Display counters cover the entire display session including
camera warm-up, overlap one another, and are not physical scanout measurements.
The large absolute NTP clock difference is separate from camera latency.

A useful next controlled check would display one visible barcode, pause it,
and verify that the other three quadrants are actually blank in the recorded
image. Resume for a separate transition sample. Paused images would test optical
clearing only and must remain excluded from timing calibration. This can help
distinguish lingering image content from the current-indicator geometry problem.

## Reproduction and evidence

From the repository root, the saved corners are reused without reopening the picker:

```bash
python3 -B analyze_calibration_recording_offset.py recordings/new_calibration \
  --alpha .25 --output-dir recordings/new_calibration_analysis/alpha_025
python3 -B analyze_calibration_recording_offset.py recordings/newer_calibration \
  --alpha .25 --output-dir recordings/newer_calibration_analysis/alpha_025
python3 -B -m calibration.analysis.summarize_manual_analysis \
  recordings/new_calibration_analysis/alpha_025/calibration_offset_analysis.json \
  recordings/newer_calibration_analysis/alpha_025/calibration_offset_analysis.json \
  --output recordings/manual_calibration_analysis_20260903/summary.json
```

Each analysis directory contains the full JSON, accepted-frame CSV and diagnostics
CSV for every recorded frame. The parent directories contain the alpha-0 baseline,
timing overview and `paired_decoding` sample evidence. The combined summary retains
per-frame coverage and hashes of its source reports. The regular report's legacy
`decoded_frames` / `decode_failures` aliases refer to accepted/excluded timing
results; use the combined summary's `coverage` fields for actual barcode coverage.
