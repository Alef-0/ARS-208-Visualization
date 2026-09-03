"""Manual geometry and decoding checks; no windows or generated clicks."""

import json
from pathlib import Path
import random
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2 as cv
import numpy as np

from calibration.decoding.regions import ManualRegions, middle_index, validate_quads
from calibration.decoding.markers import ManualPanelAnalyzer, rectangle_points
from calibration.inspection.data import Undistorter
from calibration.inspection.data import CalibrationRecording
from calibration.analysis.recording import analyze_recording
from tests.test_calibration_marker_analysis import fixture


class ManualRegionTests(unittest.TestCase):
    def test_middle_frame_and_invalid_selections(self):
        self.assertEqual(middle_index(1), 0)
        for _ in range(100):
            self.assertTrue(39 <= middle_index(100, random.Random(_)) <= 59)
        with self.assertRaises(ValueError):
            middle_index(0)
        _, evidence, _ = fixture()
        quads = np.array([rectangle_points(l["barcode"]) for l in evidence.metadata["layouts"]])
        validate_quads(quads, (1920, 1080))
        for bad in (quads[:, ::-1], np.roll(quads, 1, axis=1), quads+2000,
                    np.repeat(quads[:1], 4, axis=0)):
            with self.assertRaises(ValueError):
                validate_quads(bad, (1920, 1080))

    def test_save_load_binds_recording_images_intrinsics_and_size(self):
        frame, evidence, _ = fixture()
        quads = np.array([rectangle_points(l["barcode"]) for l in evidence.metadata["layouts"]])
        with TemporaryDirectory() as folder:
            root = Path(folder)
            intrinsic = root/"intrinsics.json"
            intrinsic.write_text(json.dumps({"camera_matrix": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
                                            "dist_coeffs": [0]*5, "image_size": [1920, 1080]}))
            (root/"camera_timestamps.jsonl").write_text('{"frame":"frame.png"}\n')
            (root/"display_timestamps.jsonl").write_text(json.dumps(evidence.metadata)+"\n")
            cv.imwrite(str(root/"frame.png"), frame)
            model = Undistorter(intrinsic)
            regions = ManualRegions.save(root, quads, (1920, 1080), model, ["frame.png"]*16)
            np.testing.assert_allclose(regions.for_undistorter(model, (1920, 1080)), quads, atol=.001)
            with self.assertRaises(ValueError):
                regions.for_undistorter(model, (960, 540))
            (root/"camera_timestamps.jsonl").write_text('{}\n')
            with self.assertRaises(ValueError):
                ManualRegions.load(root)
            (root/"camera_timestamps.jsonl").write_text('{"frame":"frame.png"}\n')
            intrinsic.write_text('{}')
            with self.assertRaises(ValueError):
                ManualRegions.load(root)

    def test_marked_panels_bypass_detection_and_keep_transition_checks(self):
        frame, evidence, _ = fixture()
        quads = np.array([rectangle_points(l["barcode"]) for l in evidence.metadata["layouts"]])
        analyzer = ManualPanelAnalyzer(evidence, quads)
        with patch.object(analyzer.analyzer.reader, "decode", side_effect=AssertionError("No automatic discovery")):
            result = analyzer.analyze(frame, 123200)
        self.assertEqual(result["selection"], "direct", result)
        self.assertEqual(result["display_index"], 11)
        self.assertEqual(len(result["observations"]), 4)
        for o in result["observations"]:
            np.testing.assert_allclose(o["points"], rectangle_points(evidence.metadata["layouts"][o["corner"]]["bars"]), atol=.001)
        blank = np.full_like(frame, 50)
        self.assertIsNone(analyzer.analyze(blank, 123200)["screen_ns"])
        x, y, w, h = evidence.metadata["layouts"][2]["underline"]
        frame[y:y+h, x:x+w] = 255
        result = analyzer.analyze(frame, 123200)
        self.assertEqual(result["reason"], "missing_or_multiple_newest_indicators")

    def test_undistortion_padding_is_not_missing_indicator_evidence(self):
        frame, evidence, _ = fixture()
        quads = np.array([rectangle_points(l["barcode"]) for l in evidence.metadata["layouts"]])
        mask = np.ones(frame.shape[:2], dtype=bool)
        x, y, w, h = evidence.metadata["layouts"][0]["underline"]
        mask[y:y+h, x:x+w] = False
        analyzer = ManualPanelAnalyzer(evidence, quads, valid_pixels=mask)
        result = analyzer.analyze(frame, 123200)
        self.assertIsNone(result["screen_ns"])
        self.assertEqual(result["reason"], "current_indicator_outside_image")
        self.assertEqual(result["unsupported_indicator_corners"], [0])

    def test_alpha_change_maps_saved_points_to_new_projection(self):
        from types import SimpleNamespace
        with TemporaryDirectory() as folder:
            intrinsic = Path(folder)/"intrinsics.json"
            intrinsic.write_text(json.dumps({"camera_matrix": [[1100, 0, 960], [0, 1100, 540], [0, 0, 1]],
                                            "dist_coeffs": [-.35, .13, 0, 0, -.02]}))
            original, changed = Undistorter(intrinsic, alpha=0), Undistorter(intrinsic, alpha=1)
            quads = np.float32([[[200, 200], [700, 200], [700, 450], [200, 450]]]*4)
            from calibration.decoding.regions import digest
            saved = SimpleNamespace(size=(1920, 1080), quads=quads,
                                    data={"intrinsics_sha256": digest(intrinsic)},
                                    output_matrix=original.geometry((1920, 1080))[1])
            result = ManualRegions.for_undistorter(saved, changed, saved.size)
            self.assertGreater(float(np.max(abs(result-quads))), 10)
            rays_before = cv.perspectiveTransform(quads.reshape(1, -1, 2), np.linalg.inv(saved.output_matrix))
            rays_after = cv.perspectiveTransform(result.reshape(1, -1, 2), np.linalg.inv(changed.geometry(saved.size)[1]))
            np.testing.assert_allclose(rays_before, rays_after, atol=1e-6)

    def test_regular_analyzer_and_viewer_use_saved_panels(self):
        frame, evidence, _ = fixture()
        quads = np.array([rectangle_points(l["barcode"]) for l in evidence.metadata["layouts"]])
        with TemporaryDirectory() as folder:
            root = Path(folder)
            intrinsic = root/"intrinsics.json"
            intrinsic.write_text(json.dumps({"camera_matrix": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
                                            "dist_coeffs": [0]*5, "image_size": [1920, 1080]}))
            (root/"camera_timestamps.jsonl").write_text(json.dumps({"camera_frame": "frame.png", "timing": {
                "host_monotonic_received_ns": 123_300_000_000, "pipeline_age_ns": 100_000_000}})+"\n")
            journal = [{"kind": "session", **evidence.metadata}] + [{"kind": "frame", **r} for r in evidence.frames]
            (root/"display_timestamps.jsonl").write_text("".join(json.dumps(r)+"\n" for r in journal))
            cv.imwrite(str(root/"frame.png"), frame)
            ManualRegions.save(root, quads, (1920, 1080), Undistorter(intrinsic), ["frame.png"]*16)
            report = analyze_recording(root)
            self.assertEqual(report["counts"]["accepted_frames"], 1)
            self.assertEqual(report["manual_regions"]["space"], "Undistorted")
            self.assertEqual(report["analysis_undistortion_alpha"], 0)
            from calibration.analysis.summarize_manual_analysis import summarize_analysis
            report_path = root/"analysis.json"
            report_path.write_text(json.dumps(report))
            summary = summarize_analysis(report_path)
            self.assertEqual(summary["accepted_frames"], 1)
            self.assertEqual(summary["coverage"]["corners_two_bands_tl_tr_br_bl"], [1]*4)
            model = CalibrationRecording(root)
            result = model.inspect(0)
            self.assertTrue(result["prediction"]["strict"])
            self.assertTrue(all(o["variant"] == "Undistorted" for o in result["observations"]))
            self.assertEqual(model.decoders, {})


if __name__ == "__main__":
    unittest.main()
