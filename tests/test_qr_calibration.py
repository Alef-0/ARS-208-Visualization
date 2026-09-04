"""Headless QR calibration tests; no display window or camera is opened."""

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import cv2
import numpy as np
import pygame

import analyze_calibration_recording as recording_launcher
from calibration.display import QRClockRenderer, VISIBLE_QRS
from calibration.quantitative_analysis import analyze_output_directory
from calibration.qr import (
    QUIET_ZONE_MODULES,
    decode_qrs_with_quadrant_retries,
    order_by_quadrant,
    qr_matrix,
    timestamp_payload,
)
from calibration.recording_display import DEFAULT_INTRINSICS, DisplayTimeline, RecordingAnalyzer


class FakeReader:
    def __init__(self, decoded, boxes):
        self.decoded = tuple(decoded)
        self.boxes = boxes

    def detect_and_decode(self, image, return_detections=False, is_bgr=False):
        if image.shape[:2] != (100, 100):
            return ((), ()) if return_detections else ()
        detections = tuple({"bbox_xyxy": np.asarray(box, dtype=np.float32), "confidence": 0.9}
                           for box in self.boxes)
        return (self.decoded, detections) if return_detections else self.decoded


def display_rows(count=5):
    period = 16_666_667
    frames = []
    for index in range(count):
        marker = 10_000_000_000 + index * period
        frames.append({
            "kind": "frame", "index": index, "corner": index % 4,
            "marker_ns": marker, "deadline_ns": marker + 1_000_000,
            "submit_ns": marker + 500_000, "flip_return_ns": marker + 1_000_000,
            "frame_period_ns": period, "interval_ns": period if index else None,
            "late_submit": False, "skipped_periods": 0, "irregular_interval": False,
            "resumed_after_pause": False,
        })
    return [{"kind": "session", "format": "segcom-qr-display-v1"}, *frames]


class QRHelpersTests(unittest.TestCase):
    def test_payload_and_reduced_quiet_zone(self):
        self.assertEqual(timestamp_payload(12_345_678_900_000), "000012345678")
        matrix = qr_matrix("000012345678")
        self.assertEqual(QUIET_ZONE_MODULES, 2)
        self.assertFalse(matrix[:QUIET_ZONE_MODULES].any())
        self.assertFalse(matrix[:, :QUIET_ZONE_MODULES].any())

    def test_bounding_boxes_are_ordered_clockwise_by_sector(self):
        detections = [
            {"raw": "3", "bbox": np.array([10, 60, 30, 80]), "center": (20, 70), "confidence": 1},
            {"raw": "1", "bbox": np.array([10, 10, 30, 30]), "center": (20, 20), "confidence": 1},
            {"raw": "4", "bbox": np.array([60, 60, 80, 80]), "center": (70, 70), "confidence": 1},
            {"raw": "2", "bbox": np.array([60, 10, 80, 30]), "center": (70, 20), "confidence": 1},
        ]
        ordered = order_by_quadrant(detections, (100, 100))
        self.assertEqual([item["raw"] for item in ordered], ["1", "2", "4", "3"])
        self.assertEqual([item["quadrant"] for item in ordered], [0, 1, 2, 3])

    def test_missing_quadrant_is_retried_with_qreader(self):
        class RetryReader:
            def __init__(self):
                self.calls = []

            def detect_and_decode(self, image, return_detections=False, is_bgr=False):
                self.calls.append(image.shape[:2])
                if image.shape[:2] == (100, 100):
                    decoded = ("1", "2", "3")
                    boxes = ([10, 10, 30, 30], [60, 10, 80, 30], [60, 60, 80, 80])
                else:
                    decoded = ("4",) if len(self.calls) == 2 else ()
                    boxes = ([10, 10, 30, 30],) if decoded else ()
                detections = tuple({
                    "bbox_xyxy": np.asarray(box, dtype=np.float32),
                    "confidence": 0.9,
                } for box in boxes)
                return (decoded, detections) if return_detections else decoded

        reader = RetryReader()
        detections = decode_qrs_with_quadrant_retries(
            reader, np.zeros((100, 100, 3), np.uint8)
        )
        ordered = order_by_quadrant(detections, (100, 100))
        self.assertEqual([item["raw"] for item in ordered], ["1", "2", "3", "4"])
        self.assertEqual(reader.calls, [(100, 100), (50, 50)])

    def test_renderer_keeps_exactly_two_quadrants(self):
        renderer = QRClockRenderer(pygame.Surface((960, 540)))
        for index in range(4):
            renderer.render_next(10_000_000_000 + index * 20_000_000)
        self.assertEqual(VISIBLE_QRS, 2)
        self.assertEqual(sum(value is not None for value in renderer.timestamps), 2)
        self.assertEqual(renderer.metadata()["visible_qrs"], 2)


class RecordingTests(unittest.TestCase):
    def fixture(self, folder: Path, count=5):
        rows = display_rows(count)
        (folder / "display_timestamps.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
        frame = np.zeros((100, 100, 3), np.uint8)
        cv2.imwrite(str(folder / "camera_000001.jpg"), frame)
        (folder / "camera_timestamps.jsonl").write_text(json.dumps({
            "frame": "camera_000001.jpg", "stream_epoch": 1,
            "pts_ns": 100_000_000, "reference_ntp_ns": 1_700_000_000_000_000_000,
        }) + "\n", encoding="utf-8")
        (folder / "camera_timing_session.json").write_text(json.dumps({
            "epochs": [{"stream_epoch": 1, "pipeline_zero_monotonic_ns": 9_950_000_000}]
        }), encoding="utf-8")
        return rows

    def four_value_model(self, folder: Path, count=5):
        rows = self.fixture(folder, count)
        frames = rows[1:]
        selected = (frames[4], frames[1], frames[2], frames[3])
        boxes = ([10, 10, 30, 30], [60, 10, 80, 30],
                 [60, 60, 80, 80], [10, 60, 30, 80])
        reader = FakeReader([timestamp_payload(row["marker_ns"]) for row in selected], boxes)
        return RecordingAnalyzer(folder, DEFAULT_INTRINSICS, reader=reader), frames

    def test_timeline_checks_following_replacement(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rows = self.fixture(folder)
            timeline = DisplayTimeline(folder)
            self.assertEqual(timeline.marker_status(2), ("Clean", []))
            rows[4]["late_submit"] = True
            (folder / "display_timestamps.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
            )
            timeline = DisplayTimeline(folder)
            status, issues = timeline.marker_status(2)
            self.assertEqual(status, "Timing suspect")
            self.assertIn("replacement_late_submission", issues)

    def test_qreader_box_is_matched_and_quadrant_mismatch_is_reported(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rows = self.fixture(folder)
            marker = rows[3]
            reader = FakeReader([timestamp_payload(marker["marker_ns"])], [[10, 10, 30, 30]])
            model = RecordingAnalyzer(folder, DEFAULT_INTRINSICS, reader=reader)
            result = model.analyze(0)
            self.assertEqual(result["latest"]["display_index"], marker["index"])
            self.assertTrue(result["latest"]["mismatch"])
            self.assertEqual(result["latest"]["status"], "Quadrant mismatch")

    def test_four_values_must_form_one_clockwise_consecutive_sequence(self):
        with TemporaryDirectory() as temporary:
            model, frames = self.four_value_model(Path(temporary), count=9)
            result = model.analyze(0)
            self.assertTrue(model.check_frame(result)["valid"])
            model.set_manual_values(0, {
                "pts_ns": result["row"]["pts_ns"],
                "ntp_ns": result["row"]["reference_ntp_ns"],
                "qrs": (
                    timestamp_payload(frames[8]["marker_ns"]),
                    timestamp_payload(frames[1]["marker_ns"]),
                    timestamp_payload(frames[2]["marker_ns"]),
                    timestamp_payload(frames[3]["marker_ns"]),
                ),
            })
            check = model.check_frame(result)
            self.assertFalse(check["valid"])
            self.assertIn("not one consecutive clockwise sequence", check["reason"])

    def test_scan_skips_frame_with_missing_quadrant(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary)
            rows = self.fixture(folder)
            frames = rows[1:]
            reader = FakeReader(
                [timestamp_payload(frames[index]["marker_ns"]) for index in (4, 1, 2)],
                [[10, 10, 30, 30], [60, 10, 80, 30], [60, 60, 80, 80]],
            )
            model = RecordingAnalyzer(folder, DEFAULT_INTRINSICS, reader=reader)
            report = model.summarize(0.25, threading.Event(), lambda *_: None)
            self.assertEqual(report["processed"], 1)
            self.assertIsNone(report["stopped"])
            self.assertEqual(report["counts"]["skipped_incomplete_frames"], 1)

    def test_analysis_results_are_saved_beside_recording(self):
        with TemporaryDirectory() as temporary:
            folder = Path(temporary) / "sample_recording"
            folder.mkdir()
            model, _ = self.four_value_model(folder)
            report = model.summarize(0.25, threading.Event(), lambda *_: None)
            saved = model.save_report(report)
            output = Path(temporary) / "sample_recording_analysis"
            self.assertEqual(saved["output_directory"], str(output))
            json_path = output / "calibration_analysis.json"
            csv_path = output / "calibration_frames.csv"
            self.assertTrue(json_path.is_file())
            self.assertTrue(csv_path.is_file())
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["counts"]["accepted_frames"], 1)
            self.assertEqual(loaded["frames"][0]["validation"], "accepted_unknown")
            self.assertIn("qr_top_left_ms", csv_path.read_text(encoding="utf-8"))


class QuantitativeVerdictTests(unittest.TestCase):
    def test_saved_analysis_produces_verdict_metrics_and_svg_graphs(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary)
            frames = []
            for index in range(45):
                clean = index != 8
                frames.append({
                    "frame_number": index + 1,
                    "filename": f"camera_{index + 1:06d}.jpg",
                    "validation": "accepted_clean" if clean else "accepted_timing_suspect",
                    "timing_status": "Clean" if clean else "Timing suspect",
                    "pts_ns": index * (20_000_000 if index % 2 else 40_000_000),
                    "pts_minus_latest_qr_ms": 96.0 + index % 4,
                })
            source = {
                "recording_directory": "/recordings/sample",
                "processed": len(frames),
                "display": {
                    "late_submissions": 2,
                    "irregular_intervals": 1,
                    "missed_period_candidates": 0,
                },
                "frames": frames,
            }
            (output / "calibration_analysis.json").write_text(
                json.dumps(source), encoding="utf-8"
            )
            with (output / "calibration_frames.csv").open(
                "w", encoding="utf-8", newline=""
            ) as destination:
                writer = csv.DictWriter(destination, fieldnames=frames[0].keys())
                writer.writeheader()
                writer.writerows(frames)

            def graph_file(path, *_args, **_kwargs):
                path.write_text("<svg/>", encoding="utf-8")
                path.with_suffix(".png").write_bytes(b"PNG")

            with (
                patch("calibration.quantitative_analysis._write_timeline_graph", side_effect=graph_file),
                patch("calibration.quantitative_analysis._write_residual_graph", side_effect=graph_file),
                patch("calibration.quantitative_analysis._write_histogram", side_effect=graph_file),
            ):
                report = analyze_output_directory(output)

            self.assertEqual(report["data_quality"]["clean_frames"], 44)
            self.assertEqual(report["data_quality"]["timing_suspect_frames"], 1)
            self.assertLess(
                report["verdict"]["recommended_fixed_correction_ms"],
                report["verdict"]["current_correction_ms"],
            )
            self.assertTrue((output / "calibration_verdict.md").is_file())
            self.assertIn("<svg", (output / "calibration_offset_timeline.svg").read_text())
            for filename in (
                "calibration_offset_histogram.svg",
                "calibration_fixed_residual_histogram.svg",
                "calibration_pts_residual_histogram.svg",
            ):
                self.assertIn("<svg", (output / filename).read_text())
                self.assertTrue((output / Path(filename).with_suffix(".png")).is_file())

    def test_root_launcher_runs_saved_data_verdict_after_the_window(self):
        recording = Path("/recordings/sample")
        output = Path("/recordings/sample_analysis")
        with (
            patch.object(recording_launcher, "run_recording_display", return_value=output) as display,
            patch.object(recording_launcher, "_run_quantitative_analysis", return_value={
                "output_directory": str(output)
            }) as analyze,
            patch("sys.argv", ["analyze_calibration_recording.py", str(recording)]),
        ):
            recording_launcher.main()
        display.assert_called_once()
        analyze.assert_called_once_with(output)


if __name__ == "__main__":
    unittest.main()
