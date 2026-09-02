"""Exercise GUI orchestration with fake processes; never connect/open a UI."""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import main


class CalibrationWorkflowTests(unittest.TestCase):
    def fixture(self):
        process = Mock()
        process.is_alive.return_value = True
        process.exitcode = 0
        context = Mock()
        context.Process.return_value = process
        runtime = main.RuntimeState(process_context=context)
        config = SimpleNamespace(calibration_camera=True, connected_cam=True,
                                 calibration_recording=False,
                                 show_calibration_error=Mock(), change_calibration_clock=Mock(),
                                 window={"calibration_status": Mock()})
        return config, runtime, process

    def test_display_journal_destination_precedes_delayed_camera_recording(self):
        config, runtime, process = self.fixture()
        camera = Mock()
        with TemporaryDirectory() as folder, patch.object(main.time, "monotonic", return_value=100):
            main._start_calibration_clock({"record_folder": folder}, config, runtime)
            destination = Path(runtime.calibration_prepared_folder)
            self.assertTrue(destination.is_dir())
            arguments = runtime.process_context.Process.call_args.kwargs
            self.assertEqual(arguments["kwargs"]["journal_path"], str(destination / "display_timestamps.jsonl"))
            self.assertEqual(arguments["kwargs"]["visible_frames"], 3)
            main._service_calibration(config, runtime, camera)
            camera.send.assert_not_called()
            with patch.object(main.time, "monotonic", return_value=103):
                main._service_calibration(config, runtime, camera)
            camera.send.assert_called_once_with(("record_start", {
                "folders": {4: str(destination)}, "calibration": True,
                "display_journal": "display_timestamps.jsonl"}))
            self.assertEqual(runtime.calibration_recording_folder, str(destination))
            config.calibration_recording = True
            process.is_alive.return_value = False
            main._service_calibration(config, runtime, camera)
            camera.send.assert_called_with(("record_stop", None))

    def test_closing_before_deadline_cancels_capture_but_preserves_destination(self):
        config, runtime, process = self.fixture()
        with TemporaryDirectory() as folder:
            main._start_calibration_clock({"record_folder": folder}, config, runtime)
            destination = Path(runtime.calibration_prepared_folder)
            process.is_alive.return_value = False
            camera = Mock()
            main._service_calibration(config, runtime, camera)
            camera.send.assert_not_called()
            self.assertIsNone(runtime.calibration_recording_deadline)
            self.assertIsNone(runtime.calibration_prepared_folder)
            self.assertTrue(destination.exists())

    def test_failed_start_does_not_schedule_recording(self):
        config, runtime, process = self.fixture()
        process.start.side_effect = OSError("process start failed")
        with TemporaryDirectory() as folder:
            main._start_calibration_clock({"record_folder": folder}, config, runtime)
        self.assertIsNone(runtime.calibration_clock_process)
        self.assertIsNone(runtime.calibration_recording_deadline)
        config.show_calibration_error.assert_called_once()

    def test_visible_frame_count_is_forwarded_and_invalid_values_do_not_start(self):
        for count in range(1, 5):
            with self.subTest(count=count):
                config, runtime, process = self.fixture()
                config.calibration_camera = False
                main._start_calibration_clock({"calibration_visible_frames": str(count)}, config, runtime)
                self.assertEqual(runtime.process_context.Process.call_args.kwargs["kwargs"]["visible_frames"], count)
        for count in ("0", "5", "1.5", "invalid"):
            with self.subTest(count=count):
                config, runtime, process = self.fixture()
                main._start_calibration_clock({"calibration_visible_frames": count}, config, runtime)
                runtime.process_context.Process.assert_not_called()
                config.show_calibration_error.assert_called_once()

    def test_visible_barcode_control_defaults_to_three(self):
        def elements(rows):
            for row in rows:
                for element in row:
                    yield element
                    nested = getattr(element, "Rows", None)
                    if nested:
                        yield from elements(nested)
        control = next(element for element in elements(main.Configurations._create_calibration_layout())
                       if getattr(element, "Key", None) == "calibration_visible_frames")
        self.assertEqual(tuple(control.Values), (1, 2, 3, 4))
        self.assertEqual(control.DefaultValue, 3)


if __name__ == "__main__":
    unittest.main()
