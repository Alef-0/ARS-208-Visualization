import unittest

from CALIBRATION.calibration_screen_clock import (
    DISPLAY_FRAME_NS,
    format_monotonic_timestamp,
    predicted_display_time_ns,
)
from CALIBRATION.ean13 import (
    ean13_bits,
    ean13_check_digit,
    monotonic_ms_payload,
)


class CalibrationClockTests(unittest.TestCase):
    def test_monotonic_time_becomes_a_twelve_digit_payload(self):
        self.assertEqual(monotonic_ms_payload(123_456_789_000_000), "000123456789")

    def test_ean13_encoding_has_valid_size_and_guards(self):
        payload = "123456789012"
        self.assertEqual(ean13_check_digit(payload), "8")
        bits = ean13_bits(payload)
        self.assertEqual(len(bits), 95)
        self.assertTrue(bits.startswith("101"))
        self.assertTrue(bits.endswith("101"))

    def test_display_label_and_prediction_use_monotonic_time(self):
        timestamp_ns = 12_345_678_000_000
        self.assertEqual(format_monotonic_timestamp(timestamp_ns), "12 345.678")
        self.assertEqual(
            predicted_display_time_ns(timestamp_ns),
            timestamp_ns + DISPLAY_FRAME_NS,
        )


if __name__ == "__main__":
    unittest.main()
