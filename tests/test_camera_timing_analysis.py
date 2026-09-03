import unittest

from calibration.analysis.recording import (
    ean13_check_digit,
    normalize_ean13,
    normalize_timing_row,
    summarize,
    unwrap_monotonic_ms,
)


class CameraTimingAnalysisTests(unittest.TestCase):
    def test_normalizes_ean13_and_restores_upca_leading_zero(self):
        payload = "123456789012"
        code = payload + ean13_check_digit(payload)
        self.assertEqual(normalize_ean13(code, "EAN_13"), code)

        zero_payload = "012345678901"
        zero_code = zero_payload + ean13_check_digit(zero_payload)
        self.assertEqual(normalize_ean13(zero_code[1:], "UPC_A"), zero_code)

    def test_unwraps_encoded_monotonic_time_near_reference(self):
        modulus = 10**12
        reference = modulus + 125
        self.assertEqual(unwrap_monotonic_ms(120, reference), modulus + 120)

    def test_compact_row_uses_the_pipeline_epoch_anchor(self):
        row = {
            "frame": "camera_000001.jpg",
            "stream_epoch": 4,
            "pts_ns": 3_000_000_000,
            "running_time_ns": 2_000_000_000,
            "received_monotonic_ns": 20_000_000_000,
            "received_unix_ns": 1_800_000_010_000_000_100,
            "media_unix_ns": 1_800_000_002_000_000_000,
            "reference_ntp_ns": 1_800_000_000_000_000_000,
        }
        epochs = {
            4: {
                "stream_epoch": 4,
                "pipeline_zero_monotonic_ns": 10_000_000_000,
                "pipeline_zero_unix_ns": 1_800_000_000_000_000_000,
            },
        }

        normalized = normalize_timing_row(row, epochs)

        self.assertEqual(normalized["camera_frame"], "camera_000001.jpg")
        self.assertEqual(normalized["frame_monotonic_ns"], 12_000_000_000)
        self.assertEqual(
            normalized["reference_ntp_ns"],
            1_800_000_000_000_000_000,
        )
        self.assertEqual(normalized["system_clock_error_ns"], 100)

    def test_legacy_row_uses_receipt_minus_pipeline_age(self):
        row = {
            "camera_frame": "camera_000002.jpg",
            "timing": {
                "pts_ns": 8_000_000_000,
                "running_time_ns": 7_000_000_000,
                "host_monotonic_received_ns": 30_000_000_000,
                "pipeline_age_ns": 145_000_000,
            },
        }

        normalized = normalize_timing_row(row, {})

        self.assertEqual(normalized["frame_monotonic_ns"], 29_855_000_000)

    def test_summary_reports_distribution_and_ignores_missing_values(self):
        summary = summarize([1.0, 2.0, None, 4.0])

        self.assertEqual(summary["count"], 3)
        self.assertEqual(summary["median"], 2.0)
        self.assertEqual(summary["minimum"], 1.0)
        self.assertEqual(summary["maximum"], 4.0)


if __name__ == "__main__":
    unittest.main()
