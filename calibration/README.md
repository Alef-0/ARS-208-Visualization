# Camera timing calibration

This package contains the display, OpenCV decoding, recording analysis and
manual inspection tools. Begin with a recording and mark each barcode panel
on an undistorted middle frame; the saved geometry is specific to that recording.
Readable codes remain separate from accepted timing measurements.

## Package and documentation map

| Folder | Responsibility and guide |
|---|---|
| [display/](display/README.md) | Persistent fullscreen canvas, EAN-13 rendering, pacing and display journals |
| [decoding/](decoding/README.md) | OpenCV reads, lens correction, fixed/learned regions and conservative marker selection |
| [inspection/](inspection/README.md) | Viewer, manual corner picker and cancellable background scans |
| [analysis/](analysis/README.md) | Regular offset reports, decoder comparisons and coverage summaries |
| [analysis/experiments/](analysis/README.md#historical-studies) | Earlier recording-specific audits and timing predictors |
| [docs/reports/](docs/reports/README.md) | Dated results and their reproduction instructions |

`paths.py` provides shared project-path and intrinsic-file suggestions.
Recorded images, saved panel coordinates and generated analysis outputs remain
under the ignored `recordings/` directory, outside source packages.

## Start here

Use the application's Calibration tab to record camera 4 with the barcode
display. Use its Visualization tab or the existing root launcher to inspect it:

```bash
python3 visualize_calibration_recording.py /path/to/recording
python3 analyze_calibration_recording_offset.py /path/to/recording \
  --output-dir /path/to/analysis
```

To mark regions separately, or run only the display:

```bash
python3 -m calibration.inspection.region_picker /path/to/recording
python3 -m calibration.display.clock --refresh-hz 60 \
  --journal /path/to/new-display-timestamps.jsonl
```

The viewer and analyzer launchers retain their original names. Python imports
and module commands now use the lowercase `calibration` package; the old
`CALIBRATION.*` and `processing.visualization.calibration_*` module paths moved.

## Checkpoint: 3 September 2026

The [manual-panel study](docs/reports/2026-09-03-manual-panels.md) recovered all
four codes in 1,537/1,894 frames of `new_calibration` and 1,080/1,081 frames of
`newer_calibration` at alpha 0.25, requiring two independent bands per panel.
Both recordings still yielded zero accepted timing measurements because of
overlapping display states and indicator/timing ambiguity. The existing camera
correction remains unchanged. These findings are specific to those recordings.

The [test suite map](../tests/README.md) describes automated coverage. Tests use
generated pixels and fake devices; the owner verifies actual UI appearance.
