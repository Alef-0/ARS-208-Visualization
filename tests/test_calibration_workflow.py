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
            self.assertNotIn("visible_frames", arguments["kwargs"])
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

    def test_calibration_layout_has_fixed_qr_mode_without_amount_control(self):
        def elements(rows):
            for row in rows:
                for element in row:
                    yield element
                    nested = getattr(element, "Rows", None)
                    if nested:
                        yield from elements(nested)
        controls = list(elements(main.Configurations._create_calibration_layout()))
        self.assertFalse(any(getattr(element, "Key", None) == "calibration_visible_frames"
                             for element in controls))
        button = next(element for element in controls
                      if getattr(element, "Key", None) == "calibration_clock_start")
        self.assertEqual(button.ButtonText, "START QR CALIBRATION")

    def test_visualization_launches_single_analyzer_with_only_the_folder(self):
        with TemporaryDirectory(prefix="qr calibration ") as folder:
            path = Path(folder)
            (path / "camera_timestamps.jsonl").write_text("{}\n")
            runtime = main.RuntimeState()
            config = SimpleNamespace(window={"visualization_open": Mock(), "visualization_status": Mock()})
            with patch.object(main.subprocess, "Popen") as launch:
                main._start_calibration_visualization({"visualization_folder": folder}, config, runtime)
            command = launch.call_args.args[0]
            self.assertEqual(Path(command[1]).name, "analyze_calibration_recording.py")
            self.assertEqual(command[2:], [str(path.resolve())])


if __name__ == "__main__":
    unittest.main()
