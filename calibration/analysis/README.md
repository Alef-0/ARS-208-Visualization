# Recording analysis

Run these commands from the repository root. The regular analyzer opens the
manual picker when a display journal exists but no saved selection is available.
See [marking instructions](../inspection/README.md#manual-selection-and-saved-geometry).

```bash
python3 analyze_calibration_recording_offset.py /path/to/recording \
  --output-dir /path/to/analysis
```

The implementation is `recording.py`; `python3 -m calibration.analysis.recording`
provides the same command. `--mark-panels` replaces an existing selection,
`--intrinsics` selects lens coefficients, and `--alpha .25` remaps saved corners
without changing the marking file. Use separate output directories for comparisons.
`--automatic` explicitly requests automatic registration. `--screen-corners`
is the legacy whole-monitor mapping in original image coordinates.

The analysis outputs now include:

- `calibration_offset_analysis.json`: display-session totals and reconstructed
  events, per-issue camera-frame counts, exclusion-reason counts, and diagnostics
  for every camera image. Events are reconstructed from raw frame rows, so older
  journals work and recorded event rows are not double-counted. Display totals
  include the warm-up; camera counts cover only recorded images. Issue counts
  overlap and must not be added as unique lost-frame counts.
- `calibration_offset_frames.csv`: accepted offset measurements with their
  display timing assessment; rejected samples never enter offset statistics.
- `calibration_frame_diagnostics.csv`: every recorded camera image, its
  acceptance decision/reason, selected display index, timing status, issue codes,
  and detailed timing evidence (structured values are JSON inside CSV cells).

Old recordings without any display journal remain explicitly labelled
`unavailable_legacy` in diagnostics; no display timing assurance is implied.

## Decoder sensitivity study

```bash
python3 -m calibration.analysis.evaluate_decoding recordings/calibration_third \
  --intrinsics "/path/to/intrinsic_coefficients.json" \
  --step 25 --alphas 0 .25 .5 .75 1 --binary \
  --output-dir recordings/calibration_third_analysis/sensitivity
```

Use `--step 1 --alphas 0` for a full comparison of original and alpha-0 images.
When a manual selection exists, the comparison also evaluates its fixed panels
at every requested alpha, reporting grayscale, local contrast and optional
binary results separately, including coverage for each corner. Those coverage
counts require a journal match but do not imply an accepted timing measurement.
The program opens no windows and changes no input recordings. `summary.json`
reports coverage, conflicting panels, first-two-thirds/last-third results,
paired original/undistorted newest reads and learned location stability.
`observations.jsonl` retains accepted/rejected observations, preprocessing,
regions and image hashes. These are optical capability measurements; the newest
decoded code is not a verified exposure timestamp or a new timing correction.

## Summarize a manual analysis

```bash
python3 -m calibration.analysis.summarize_manual_analysis \
  /path/to/analysis/calibration_offset_analysis.json \
  --output /path/to/analysis/coverage_summary.json
```

The summary reads existing results and journals without reopening images.
It separates one-band and two-band barcode coverage from accepted timing
measurements, records chronological thirds and preserves source report hashes.
The regular report's historical `decoded_frames` and `decode_failures` fields
are aliases for timing acceptance/rejection; use this coverage summary when
asking how many frames actually contain readable barcodes.

## Historical studies

`experiments/analyze_evidence.py` and `experiments/analyze_patterns.py` reproduce
the early `calibration_first` / `calibration_second` audits and timing-pattern
experiments. They require those recordings and, for fitting, the corresponding
cached observations and numerical-analysis environment. They are not part of
the live display or the current viewer. See the
[timing-pattern report](../docs/reports/2026-09-03-timing-patterns.md) for inputs,
commands, limitations and historical decoder differences.
