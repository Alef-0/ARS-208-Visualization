from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import main


class FakePipe:
    def __init__(self):
        self.sent = []

    def send(self, value):
        self.sent.append(value)


class FakeConfig:
    calibration_camera = False
    calibration_recording = False
    connected_cam = False
    connected_radar = True
    playback = False
    playback_pending = False
    recording = False
    recording_pending = False
    snapshot_playback = False
    snapshot_playback_pending = False

    def __init__(self):
        self.pending = False
        self.errors = []

    def set_calibration_camera_pending(self):
        self.pending = True

    def show_calibration_error(self, message):
        self.errors.append(message)

    def change_calibration_clock(self, active):
        self.calibration_clock = bool(active)


class FinishedProcess:
    @staticmethod
    def is_alive():
        return False

    @staticmethod
    def join(timeout=None):
        return None


class CalibrationControlTests(unittest.TestCase):
    def test_camera_four_waits_until_radars_are_closed(self):
        config = FakeConfig()
        runtime = main.RuntimeState()
        radar = FakePipe()
        camera = FakePipe()

        main._request_calibration_camera(
            {
                "camera_pipeline_latency": "100",
                "camera_latency_adjustment": "-25.5",
                "camera_recording_interval": "33.333",
            },
            config,
            runtime,
            radar,
            camera,
            FakePipe(),
            FakePipe(),
        )

        self.assertTrue(config.pending)
        self.assertEqual(radar.sent, [("conn_radar", None)])
        self.assertEqual(camera.sent, [])

        config.connected_radar = False
        main._maybe_open_calibration_camera(config, runtime, camera)

        self.assertEqual(camera.sent[0][0], "camera_latency_settings")
        self.assertEqual(camera.sent[0][1]["pipeline_latency_ms"], 100)
        self.assertEqual(
            camera.sent[1],
            ("camera_recording_interval", {"interval_ms": 33.333}),
        )
        self.assertEqual(camera.sent[2], ("calibration_camera", {"active": True}))

    def test_delayed_calibration_recording_is_camera_only(self):
        config = FakeConfig()
        config.calibration_camera = True
        config.connected_cam = True
        config.connected_radar = False
        runtime = main.RuntimeState(calibration_recording_deadline=0.0)
        camera = FakePipe()

        with TemporaryDirectory() as folder:
            runtime.calibration_recording_root = folder
            main._service_calibration(config, runtime, camera)
            created = Path(runtime.calibration_recording_folder)

        self.assertTrue(created.name.startswith("calibration_camera_4_"))
        self.assertEqual(camera.sent[0][0], "record_start")
        self.assertEqual(camera.sent[0][1]["calibration"], True)
        self.assertEqual(set(camera.sent[0][1]["folders"]), {4})

    def test_closing_fullscreen_clock_stops_calibration_recording(self):
        config = FakeConfig()
        config.calibration_camera = True
        config.calibration_recording = True
        config.connected_cam = True
        config.connected_radar = False
        runtime = main.RuntimeState(
            calibration_recording_deadline=999999.0,
            calibration_recording_root="/unused",
            calibration_recording_folder="/recording",
            calibration_clock_process=FinishedProcess(),
        )
        camera = FakePipe()

        main._service_calibration(config, runtime, camera)

        self.assertEqual(camera.sent, [("record_stop", None)])
        self.assertIsNone(runtime.calibration_recording_deadline)
        self.assertIsNone(runtime.calibration_recording_root)
        self.assertFalse(config.calibration_clock)


if __name__ == "__main__":
    unittest.main()
