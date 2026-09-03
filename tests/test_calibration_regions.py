"""OpenCV region learning, artifact rejection and camera-geometry regressions."""

import unittest
from unittest.mock import patch
from tempfile import TemporaryDirectory

import cv2 as cv
import numpy as np

from calibration.decoding.opencv import RegionDecoder, normalized_code
from calibration.decoding.markers import rectangle_points
from tests.test_calibration_marker_analysis import fixture
from tests.test_calibration_visualization import display, observation
from tests import test_calibration_visualization as fixtures
from calibration.inspection.data import choose_prediction, Undistorter


class RegionTests(unittest.TestCase):
    def test_learned_regions_never_reuse_previous_payload_on_blank_frame(self):
        frame, evidence, _ = fixture()
        decoder = RegionDecoder(evidence)
        layout = evidence.metadata["layouts"][3]
        payload = f"{evidence.frames[11]['marker_ns']//1_000_000:012d}"
        from calibration.display.ean13 import ean13_check_digit
        code = payload + ean13_check_digit(payload)
        symbol = {"raw_code": code, "type": "EAN_13",
                  "points": rectangle_points(layout["barcode"])[[3, 0, 1, 2]].tolist()}
        with patch.object(decoder.reader, "decode", return_value=[symbol]):
            decoder.decode(frame, 123200, index=0)
        with patch.object(decoder.reader, "decode", return_value=[]):
            recovered = decoder.decode(frame, 123200, index=1)
            self.assertIn(code, {normalized_code(s["raw_code"], s["type"]) for s in recovered})
            self.assertEqual(decoder.decode(np.full_like(frame, 50), 123200, index=2), [])
        self.assertEqual(len(decoder.regions()), 1)
        with patch.object(decoder.reader, "decode", return_value=[]):
            decoder.decode(frame, 123200, index=20)
        self.assertEqual(decoder.regions(), [])

    def test_merged_boxes_do_not_reset_stationary_layout_but_common_motion_does(self):
        evidence = display()
        decoder = RegionDecoder(evidence, contrast=False)
        frame = np.zeros((600, 1000, 3), np.uint8)
        symbols = []
        for index, x in ((4, 10), (5, 450)):
            obs, _ = observation(evidence, index)
            symbols.append({"raw_code": obs["code"], "type": "EAN_13",
                            "points": rectangle_points((x, 10, 350, 200))[[3, 0, 1, 2]].tolist()})
        with patch.object(decoder.reader, "decode", return_value=symbols), patch.object(decoder.reader, "decode_regions", return_value=[]):
            decoder.decode(frame, 123200, index=0)
        original = dict(decoder.regions())
        merged = {**symbols[0], "points": rectangle_points((10, 10, 790, 200))[[3, 0, 1, 2]].tolist()}
        with patch.object(decoder.reader, "decode", return_value=[merged, symbols[1]]), patch.object(decoder.reader, "decode_regions", return_value=[]):
            decoder.decode(frame, 123200, index=1)
        self.assertEqual(decoder.resets, 0)
        np.testing.assert_allclose(dict(decoder.regions())[0], original[0])
        moved = [{**s, "points": (np.array(s["points"])+[90, 70]).tolist()} for s in symbols]
        with patch.object(decoder.reader, "decode", return_value=moved), patch.object(decoder.reader, "decode_regions", return_value=[]):
            decoder.decode(frame, 123200, index=2)
        self.assertEqual(decoder.resets, 1)
        np.testing.assert_allclose(dict(decoder.regions())[0], original[0]+[90, 70])

    def test_conflicting_bands_and_current_indicators_block_prediction(self):
        evidence = display()
        obs, timing = observation(evidence)
        for observations, strict in (([{**obs, "transition": True}], []),
                                     ([obs], [{"outlined_corners": [0, 1], "reason": "multiple_indicators"}]),
                                     ([obs], [{"reason": "expected_blank_quadrant_has_content"}])):
            result = choose_prediction(observations, strict, timing, evidence)
            self.assertIsNone(result["offset_ms"])
            self.assertFalse(result["eligible"])
        older, _ = observation(evidence, 3, timing)
        result = choose_prediction([obs, older], [], timing, evidence)
        self.assertFalse(result["eligible"])

    def test_alpha_changes_maps_and_preserves_overlay_roundtrip(self):
        with TemporaryDirectory() as folder:
            path = fixtures.GeometryAndLoadingTests().intrinsics(folder)
            points = np.array([[750., 420.], [980., 540.], [1200., 650.]])
            outputs = []
            for alpha in (0, .5, 1):
                model = Undistorter(path, alpha=alpha)
                corrected = model.points(points, "Original", "Undistorted", (1920, 1080))
                outputs.append(corrected)
                np.testing.assert_allclose(model.points(corrected, "Undistorted", "Original", (1920, 1080)), points, atol=.01)
            self.assertGreater(np.max(abs(outputs[0]-outputs[-1])), 10)
            for alpha in (-1, 2, float("nan")):
                with self.assertRaises(ValueError):
                    Undistorter(path, alpha=alpha)


if __name__ == "__main__":
    unittest.main()
