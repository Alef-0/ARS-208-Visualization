# Calibration study reports

These documents preserve dated findings, assumptions and reproduction commands.
For current usage, start with the [calibration guide](../../README.md).

| Report | Recordings and scope |
|---|---|
| [3 September: manual panels](2026-09-03-manual-panels.md) | `new_calibration`, `newer_calibration`: user-marked regions, alpha/filter comparisons, complete analysis and timing limitations |
| [3 September: OpenCV decoding](2026-09-03-decoding.md) | `calibration_third`: automatic location learning, contrast, binary filtering and alpha sensitivity |
| [3 September: timing patterns](2026-09-03-timing-patterns.md) | `calibration_first`, `calibration_second`: historical timing-feature experiments and holdout limitations |

Earlier decoder comparisons remain historical evidence; current production
decoding uses OpenCV only. Reports do not establish a correction for a new
recording. Generated images, detailed JSON/CSV and cached observations are local
artifacts under ignored `recordings/` folders and are not stored in this commit.
