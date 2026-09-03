# Calibration timing patterns — 3 September 2026

The message timestamps contain a repeatable pattern that a fixed 109 ms subtraction misses. The current PTS step and a short history of previous PTS steps are the strongest useful inputs. Receipt age adds a smaller improvement. A dynamic model fitted on the first recording reduced mean absolute residual on the second from **12.01 ms to 7.74 ms**, a **35.5% reduction** against the decoded display-marker reference.

This is the lowest observed external-recording MAE among the 22 learned models tested here, not a proven global optimum. The second recording supplied no fitting labels or coefficients, but its scores were inspected to compare model families. An independent third recording is needed to confirm the selected family. Both supplied recordings belong to the same continuing camera stream.

## Reference, inputs, and exclusions

The target correction is:

`D_i = (pipeline_zero_monotonic_ns + running_time_ns - newest_decoded_marker_ns) / 1e6`

For a predicted correction `d_i`, the evaluated residual is `D_i - d_i`. The optimization minimizes the **mean absolute residual**, not signed mean error. Its magnitude is the same as comparing the corrected PTS timestamp with the decoded marker time.

All 2,705 original image hashes were verified against the prior raw decoding audit. Cached OpenCV/ZBar reads were rejoined to the current display journal, with checksum, scanline quality, causality, coherent-generation, and marker/replacement timing checks reapplied. The numerical comparison uses 1,373 first-recording frames and 1,111 second-recording frames. The other 140/81 frames remain in the prepared inputs with exclusion reasons. No images were warped, decoded again, or overwritten for this analysis.

The target is the exact software timestamp of the newest decoded marker. A valid code can identify an older retained marker correctly when a newer marker is not decoded. No physical exposure timestamp or unique newest outline was established by these reads. Therefore residuals below describe prediction of the decoded-marker reference, not verified camera/radar alignment accuracy.

Predictors use only current and previous message timing fields. Barcode values, display indices, quadrants, marker count, image quality, and future packets are never predictor inputs. Prefix-invariance checks verify that removing later messages does not change earlier input features. Saving time was excluded because it is downstream of receipt and unavailable when assigning the original camera timestamp.

## Observed pattern

| Rounded PTS step | First: median offset | Second: median offset | Eligible frames, first / second |
|---|---:|---:|---:|
| 20 ms | 86.52 ms | 91.32 ms | 477 / 406 |
| 40 ms | 98.11 ms | 104.12 ms | 840 / 644 |
| 60 ms | 115.26 ms | 112.18 ms | 26 / 41 |

The correlation of PTS step with offset is 0.484 in the first recording and 0.619 in the second. The current PTS interval is therefore informative; it is not simply a recording-rate statistic. The average stream rate is near 30 FPS even though individual intervals are often near 20 or 40 ms.

Receipt age means `received_monotonic_ns - host_anchored_frame_monotonic_ns`, not network latency alone. Larger receipt age tends to accompany a smaller required subtraction. Its correlation with the target is −0.299 / −0.431, but much of that relationship overlaps with PTS-step information. DVR NTP differences add little to the simpler predictors and introduce another clock mapping.

## First recording as calibration; second as evaluation

All learned coefficients in this table are fitted on the first recording's 1,373 eligible frames. Every row is scored on the same 1,111 eligible second-recording frames.

| Correction | Mean absolute residual | Median absolute residual | 95th percentile absolute residual |
|---|---:|---:|---:|
| Fixed 109 ms | 12.01 ms | 11.57 ms | 24.10 ms |
| First-recording median: 94.350397 ms | 10.41 ms | 7.18 ms | 30.52 ms |
| Current PTS step | 8.52 ms | 5.44 ms | 25.97 ms |
| Current PTS step + receipt age | 7.92 ms | 4.81 ms | 24.02 ms |
| Six PTS steps + receipt age | **7.74 ms** | **4.71 ms** | **21.38 ms** |

The six-step model places 79.12% of evaluated frames within 10 ms, compared with 38.79% for 109 ms. Its mean signed residual is still +7.55 ms: it tends to predict a smaller correction than the second recording's decoded-marker offsets. This remaining bias must not be removed using the same second-recording labels and then presented as an independent test result.

Paired bootstrap resampling in 30-frame blocks gives a 95% interval of 3.32–5.24 ms for its MAE improvement over 109 ms. These intervals describe this recording, conditional on the tested model; they do not account for choosing among multiple models or restarting the camera stream.

The improvement over the two-field formula is only 0.177 ms MAE. The simpler formula is a reasonable first implementation if minimizing state and complexity matters; the history model gives the lowest observed overall MAE and a better 95th percentile here.

## Algorithms

All quantities below are in **milliseconds**. Let:

- `P_i` be the current host-anchored PTS time, calculated from the epoch's pipeline-zero monotonic anchor plus running time.
- `R_i` be the host monotonic camera-receipt time.
- `Δ_i = P_i - P_(i-1)` be the current PTS step.
- `A_i = R_i - P_i` be the receipt age.

The simplest competitive formula is:

```text
d_i = 92.318949 + 0.528968 * Δ_i - 0.105239 * A_i
corrected_time_i = P_i - d_i
```

For example, at receipt age 142 ms, it subtracts approximately 87.95 ms after a 20 ms step, 98.53 ms after a 40 ms step, or 109.11 ms after a 60 ms step. **This replaces the 109 ms subtraction; it is not an additional subtraction.** If correcting `media_unix_ns`, subtract `round(d_i * 1e6)` from that Unix timestamp while retaining the same epoch mapping.

The model with the lowest observed MAE uses the six most recent PTS intervals:

```text
d_i = 8.965367
    + 0.828934 * Δ_i
    + 0.637865 * Δ_(i-1)
    + 0.550299 * Δ_(i-2)
    + 0.423980 * Δ_(i-3)
    + 0.321957 * Δ_(i-4)
    + 0.066858 * Δ_(i-5)
    - 0.061923 * A_i

corrected_time_i = P_i - d_i
```

The decreasing weights make this a short causal timing filter: the current interval matters most, and earlier intervals provide progressively smaller adjustments. It uses approximately 200 ms of interval history without waiting for a future frame. An equivalent centered form has base 94.502021 ms, applies those weights to `Δ - 33.333333`, and applies the age coefficient to `A - 142`.

Coefficients were fitted by least absolute deviations, directly minimizing the absolute-error objective. A production implementation should fit the same form to a fresh session's calibration data and retain a separate later validation segment. The equations specify the experimental predictor, not a universal camera constant. The offline evaluation initializes unavailable earlier intervals to the nominal 33.333333 ms; real stream discontinuities require resetting history and rechecking calibration. Do not carry large reconnect gaps into the interval filter.

## Checks against overfitting and misleading patterns

An initial chronological experiment used the first 60% of recording one for fitting and the next 20% for model selection, then refitted on the first 80% and evaluated its last 20%. Its lowest-validation-MAE model included PTS step plus elapsed recording time. It achieved 10.59 ms MAE on the final first-recording segment versus 14.09 ms for 109 ms.

That apparent drift is not a safe continuing-stream rule. When the same time trend is extended to recording two without arbitrarily restarting elapsed time at the recording boundary, MAE becomes **23.64 ms**, worse than 109 ms. This is why the practical formulas above exclude elapsed recording time. Full chronological scores, including unsuccessful candidates, remain in `results.json`; they are not replaced by the more favorable full-first-to-second table.

The best six-step model's second-recording MAE also depends on optical evidence:

| Decoded subset | Frames | Fixed 109 ms MAE | Six-step model MAE |
|---|---:|---:|---:|
| Both decoders agree on newest code | 369 | 14.37 ms | 5.39 ms |
| Four distinct codes decoded | 411 | 10.05 ms | 3.58 ms |
| Three distinct codes decoded | 626 | 12.93 ms | 8.31 ms |
| One or two distinct codes decoded | 74 | 15.12 ms | 25.99 ms |

The model does not improve every subset. The larger residuals when fewer codes decode are consistent with sometimes selecting an older retained marker, although these data do not prove that explanation. Correctly reading an individual timestamp and identifying the latest visible generation are separate checks. This subset dependence is also why optimizing the overall average cannot promise the lowest absolute error for each individual frame.

## Preserved sources and reproduction

This document preserves the experiment separately from the calibration folder README. The live 109 ms correction remains the operational setting; these experimental predictors have not been enabled in acquisition. The original recordings and intrinsic file were not changed.

The reusable programs are tracked under `calibration/analysis/`. Raw recordings, cached barcode observations, tables, and plots remain local under ignored `recordings/` directories and are not included in the commit. Reproduction requires `recordings/calibration_first/` and `recordings/calibration_second/`, including their camera and display journals.

Generate the raw barcode cache first if it is absent:

```bash
python3 calibration/analysis/experiments/analyze_evidence.py
```

That command writes `recordings/calibration_analysis_20260903/`. Pattern outputs below belong to `recordings/calibration_pattern_analysis_20260903/`:

- `calibration/analysis/experiments/analyze_patterns.py`: preparation, fitting, chronological/external comparisons, causal-feature checks, metrics, and plots.
- `calibration/analysis/experiments/analyze_evidence.py`: raw OpenCV audit, exact journal matching, optional lens correction, and recording diagnostics. The historical results above also contain ZBar reads; new runs use OpenCV only.
- `prepared_frames.json`: every camera frame with joined timing and optical eligibility.
- `provenance.json`: source-journal and cached-observation hashes; individual image hashes are retained in the prepared rows.
- `results.json`: coefficients and metrics for all candidates, splits, subset checks, and bootstrap results.
- `frame_predictions.csv`: all frame features and first-80%-trained predictions.
- `second_transfer_predictions.csv`: all second-recording predictions using full-first-recording coefficients.
- `pattern_comparison.png` / `.svg`: PTS-step pattern and external absolute-residual distributions.
- `comparison.png` / `.svg`: the initial chronological-validation experiment, including the elapsed-time model.

Run preparation in the normal camera Python environment, then numerical analysis with the installed system NumPy/SciPy/Matplotlib environment:

```bash
python3 calibration/analysis/experiments/analyze_patterns.py --prepare
PYTHONNOUSERSITE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  MPLCONFIGDIR=/tmp/segcom-pattern-matplotlib \
  python3 calibration/analysis/experiments/analyze_patterns.py
```

These commands update the generated outputs under `recordings/`. Both programs accept `--output-dir`; the pattern program also accepts `--audit-dir` for a relocated barcode cache. Numerical analysis requires NumPy, SciPy, and Matplotlib; these are offline analysis dependencies, separate from the GUI requirements. The commands open no camera or window and do not build or install anything.
