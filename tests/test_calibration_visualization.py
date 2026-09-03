"""Headless timing, coordinate, worker, and GUI-launch tests; no windows/hardware."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

import main
from CALIBRATION.ean13 import ean13_check_digit
from CALIBRATION.marker_analysis import DisplayEvidence
from processing.visualization.calibration_data import (
    CalibrationRecording, Undistorter, choose_prediction, describe_observation, summarize_predictions,
)
from processing.visualization.calibration_viewer import seconds_ns
from processing.visualization.calibration_worker import InspectionWorker


def display():
    period = 16_666_667
    layouts = []
    for x, y in ((0, 0), (200, 0), (200, 100), (0, 100)):
        layouts.append({"area": [x, y, 200, 100], "barcode": [x+5, y+5, 190, 90],
                        "bars": [x+10, y+10, 180, 80], "outline": [x+2, y+2, 196, 96]})
    frames = [{"index": i, "corner": i % 4, "marker_ns": 123_000_000_000+i*period,
               "deadline_ns": 123_000_000_000+i*period+1_000_000,
               "submit_ns": 123_000_000_000+i*period+500_000,
               "flip_return_ns": 123_000_000_000+i*period+1_000_000,
               "frame_period_ns": period, "interval_ns": period if i else None,
               "late_submit": False, "skipped_periods": 0, "irregular_interval": False}
              for i in range(20)]
    return DisplayEvidence({"format": "segcom-display-v2", "size": [400, 200],
                            "outline_width": 2, "visible_frames": 3, "layouts": layouts}, frames)


def observation(evidence, index=6, timing=None, method="OpenCV", variant="Original"):
    marker = evidence.frames[index]["marker_ns"]
    timing = timing or {"frame_monotonic_ns": marker+100_000_000, "received_monotonic_ns": marker+240_000_000}
    payload = f"{marker//1_000_000:012d}"
    result = describe_observation({"raw_code": payload+ean13_check_digit(payload), "type": "EAN_13",
                                  "points": [[10, 10], [30, 10], [30, 20], [10, 20]]},
                                 method, variant, evidence, timing)
    result["id"] = f"{index}-{method}-{variant}"
    return result, timing


class PredictionTests(unittest.TestCase):
    def test_exact_journal_time_not_millisecond_rounding_or_ntp(self):
        e = display()
        obs, timing = observation(e)
        self.assertEqual(obs["marker_ns"], e.frames[6]["marker_ns"])
        self.assertEqual(obs["quadrant"], "Bottom-right")
        self.assertEqual(obs["offset_ms"], 100)
        result = choose_prediction([obs], [], timing, e)
        self.assertEqual(result["offset_ms"], 100)
        self.assertFalse(result["strict"])
        self.assertEqual(result["source_ids"], [obs["id"]])

    def test_future_checksum_valid_read_is_visible_but_never_selected(self):
        e = display()
        timing = {"frame_monotonic_ns": e.frames[6]["marker_ns"],
                  "received_monotonic_ns": e.frames[7]["marker_ns"]}
        obs, _ = observation(e, 10, timing)
        self.assertFalse(obs["valid"])
        self.assertIn("after camera receipt", obs["status"])
        self.assertIsNone(choose_prediction([obs], [], timing, e)["offset_ms"])

    def test_replacement_delay_keeps_reading_but_excludes_folder_estimate(self):
        e = display()
        e.frames[7]["irregular_interval"] = True
        obs, timing = observation(e)
        result = choose_prediction([obs], [], timing, e)
        self.assertEqual(result["offset_ms"], 100)
        self.assertFalse(result["eligible"])
        self.assertTrue(any("replacement" in warning for warning in result["warnings"]))

    def test_outline_supported_selection_tracks_source_region(self):
        e = display()
        obs, timing = observation(e, method="Outline scanlines")
        strict = [{"selection": "direct", "screen_ns": obs["payload_ms"]*1_000_000, "display_index": 6,
                   "source_ean13": obs["code"], "variant": "Original"}]
        result = choose_prediction([obs], strict, timing, e)
        self.assertTrue(result["strict"])
        self.assertEqual(result["marker_ns"], obs["marker_ns"])
        self.assertEqual(result["source_ids"], [obs["id"]])
        older, _ = observation(e, 3, timing)
        result = choose_prediction([obs, older], strict, timing, e)
        self.assertFalse(result["strict"])
        self.assertIn("one logged display state", " ".join(result["warnings"]))

    def test_conflicting_generation_reads_cannot_produce_a_best_offset(self):
        e = display()
        newest, timing = observation(e, 10)
        old, _ = observation(e, 4, timing)
        self.assertIsNone(choose_prediction([old, newest], [], timing, e)["offset_ms"])

    def test_duplicate_decoder_support_does_not_count_as_four_markers(self):
        e = display()
        a, timing = observation(e)
        b, _ = observation(e, timing=timing, method="ZBar", variant="Undistorted")
        result = choose_prediction([a, b], [], timing, e)
        self.assertEqual(result["offset_ms"], 100)
        self.assertEqual(result["source_ids"], [a["id"], b["id"]])

    def test_different_stream_epochs_and_methods_are_not_pooled(self):
        def row(epoch, strict, offset):
            return {"timing": {"stream_epoch": epoch},
                    "prediction": {"eligible": True, "strict": strict, "offset_ms": offset}}
        single = summarize_predictions([row(1, True, 90), row(1, False, 120)])
        self.assertEqual(single["outline"]["median"], 90)
        self.assertEqual(single["provisional"]["median"], 120)
        separate = summarize_predictions([row(1, False, 90), row(2, False, 190)])
        self.assertIsNone(separate["provisional"])
        self.assertEqual(separate["by_epoch"]["2"]["provisional"]["median"], 190)
        rejected = {"timing": {"stream_epoch": 2}, "prediction": {"eligible": False, "status": "Unavailable"}}
        separate = summarize_predictions([row(1, False, 90), rejected])
        self.assertEqual(separate["excluded"], {"Unavailable": 1})

    def test_monotonic_timestamp_keeps_nanoseconds(self):
        self.assertEqual(seconds_ns(4_101_234_538_582), "4101.234538582 s")


class GeometryAndLoadingTests(unittest.TestCase):
    def intrinsics(self, folder, **updates):
        data = {"camera_matrix": [[1124, 0, 978], [0, 1125, 553], [0, 0, 1]],
                "dist_coeffs": [[-.3592, .1367, .000283, -.000328, -.02586]], **updates}
        path = Path(folder)/"intrinsics.json"
        path.write_text(json.dumps(data))
        return path

    def test_overlay_roundtrip_and_matching_image_geometry(self):
        with TemporaryDirectory() as folder:
            model = Undistorter(self.intrinsics(folder))
            points = np.array([[550., 330.], [980., 540.], [1430., 760.]])
            corrected = model.points(points, "Original", "Undistorted", (1920, 1080))
            recovered = model.points(corrected, "Undistorted", "Original", (1920, 1080))
            np.testing.assert_allclose(points, recovered, atol=.01)
            self.assertGreater(np.max(abs(points-corrected)), 1)
            self.assertEqual(model.image(np.zeros((1080, 1920, 3), np.uint8)).shape, (1080, 1920, 3))

    def test_resolution_scaling_and_invalid_coefficients(self):
        with TemporaryDirectory() as folder:
            model = Undistorter(self.intrinsics(folder, image_size=[1920, 1080]))
            matrix, _, _ = model.geometry((960, 540))
            self.assertEqual(matrix[0, 0], 562)
            self.assertEqual(matrix[1, 2], 276.5)
            with self.assertRaises(ValueError):
                Undistorter(self.intrinsics(folder, dist_coeffs=[float("nan")]*5))

    def test_duplicate_and_traversal_image_names_rejected(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = root/"camera_timestamps.jsonl"
            for rows in ([{"frame": "a.jpg"}, {"frame": "a.jpg"}], [{"frame": "../outside.jpg"}]):
                manifest.write_text("\n".join(json.dumps(row) for row in rows))
                with self.assertRaises(ValueError):
                    CalibrationRecording(root)


class WorkerAndLauncherTests(unittest.TestCase):
    def test_partial_scan_is_reported_on_cancel_without_ui(self):
        worker = InspectionWorker()
        prediction = {"eligible": True, "strict": False, "offset_ms": 100}
        worker.model = SimpleNamespace(rows=[1, 2, 3], inspect=Mock(return_value={"prediction": prediction, "timing": {"stream_epoch": 1}}))
        worker.session = 4
        worker.scan = {"index": 0, "results": [], "compare": False, "errors": [], "request": 7}
        worker.scan_step()
        worker.finish_scan("Cancelled")
        session, kind, payload = worker.results.get_nowait()
        self.assertEqual((session, kind, payload["request"]), (4, "summary", 7))
        self.assertEqual(payload["summary"]["frames"], 1)
        self.assertEqual(payload["summary"]["provisional"]["median"], 100)
        self.assertIsNone(worker.scan)

    def test_launcher_uses_separate_program_and_forwards_paths(self):
        with TemporaryDirectory(prefix="calibration test ") as folder:
            path = Path(folder)
            (path/"camera_timestamps.jsonl").write_text('{}\n')
            intrinsic = path/"camera matrix.json"
            intrinsic.write_text('{}')
            runtime = main.RuntimeState()
            config = SimpleNamespace(window={"visualization_open": Mock(), "visualization_status": Mock()})
            with patch.object(main.subprocess, "Popen") as launch:
                main._start_calibration_visualization({"visualization_folder": folder,
                    "visualization_intrinsics": str(intrinsic), "visualization_undistorted": True}, config, runtime)
            command = launch.call_args.args[0]
            self.assertIn("processing.visualization.calibration_viewer", command)
            self.assertIn(str(path.resolve()), command)
            self.assertEqual(command[-3:], ["--intrinsics", str(intrinsic), "--undistorted"])
            config.window["visualization_open"].update.assert_called_once_with(disabled=True)

    def test_bad_recording_does_not_launch_and_closed_viewer_can_reopen(self):
        runtime = main.RuntimeState()
        config = SimpleNamespace(window={"visualization_open": Mock(), "visualization_status": Mock()})
        with patch.object(main.subprocess, "Popen") as launch, patch.object(main.sg, "popup_error") as popup:
            main._start_calibration_visualization({"visualization_folder": ""}, config, runtime)
            launch.assert_not_called()
            popup.assert_called_once()
        runtime.visualization_process = SimpleNamespace(poll=lambda: 0, returncode=0)
        main._service_visualization(config, runtime)
        self.assertIsNone(runtime.visualization_process)
        config.window["visualization_open"].update.assert_called_with(disabled=False)


if __name__ == "__main__":
    unittest.main()
