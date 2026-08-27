import unittest

from analyze_camera_timing import (
    OcrAttempt,
    choose_reading,
    extract_unix_candidates,
    _summary,
)


class CameraTimingAnalysisTests(unittest.TestCase):
    def test_extracts_decimal_and_missing_decimal_ocr_results(self):
        self.assertEqual(
            extract_unix_candidates("1787753589.708"),
            [1787753589.708],
        )
        self.assertEqual(
            extract_unix_candidates("1787753589708"),
            [1787753589.708],
        )

    def test_chooses_confident_candidate_near_manifest_time(self):
        reading = choose_reading(
            [
                OcrAttempt("9999999999.000", 99, "contrast"),
                OcrAttempt("1787753589.708", 82, "threshold"),
            ],
            expected_unix=1787753589.869548,
            max_difference_seconds=2,
        )

        self.assertIsNotNone(reading)
        self.assertEqual(reading.value, 1787753589.708)
        self.assertEqual(reading.preprocessing, "threshold")

    def test_summary_reports_median_latency_and_residual(self):
        results = [
            {
                "status": "ok",
                "offset_from_captured_ms": -160.0,
                "offset_from_adjusted_ms": 90.0,
                "estimated_camera_latency_ms": 160.0,
            },
            {
                "status": "ok",
                "offset_from_captured_ms": -164.0,
                "offset_from_adjusted_ms": 86.0,
                "estimated_camera_latency_ms": 164.0,
            },
            {"status": "no_reading"},
        ]

        summary = _summary(results, current_adjustment_ms=250.0)

        self.assertEqual(summary["frames_read_successfully"], 2)
        self.assertEqual(summary["median_offset_from_captured_ms"], -162.0)
        self.assertEqual(summary["median_offset_from_adjusted_ms"], 88.0)
        self.assertEqual(summary["estimated_camera_latency_ms"], 162.0)


if __name__ == "__main__":
    unittest.main()
