from datetime import datetime, timezone
import json
from pathlib import Path
import queue
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import numpy as np

from CONNECTION.connection_packages import MISSING_QUALITY, RadarPoint
import CAPTURE.camera_snapshot_recorder as camera_module
import CAPTURE.point_cloud_reader as reader_module
import CAPTURE.point_cloud_recorder as recorder_module
from CAPTURE.playback import load_recording_entries
from CAPTURE.playback import PlaybackController
from GRAPH.graph_draw import Graph_radar
import MAIN_BASE
from MENU_BASE import Configurations


class FakeWriterCloud:
    calls = []

    def __init__(self, values, fields, types):
        self.values = values
        self.fields = tuple(fields)
        self.types = tuple(types)

    @classmethod
    def from_points(cls, values, fields, types):
        cloud = cls(values.copy(), fields, types)
        cls.calls.append(cloud)
        return cloud

    def save(self, path):
        Path(path).write_bytes(b"pcd")


class FakeReaderCloud:
    current = None

    def __init__(self, fields, values):
        self.fields = tuple(fields)
        self.values = np.asarray(values)

    @classmethod
    def from_path(cls, _path):
        return cls.current

    def numpy(self, fields):
        indexes = [self.fields.index(field) for field in fields]
        return self.values[:, indexes]


class RecordingChangesTests(unittest.TestCase):
    def setUp(self):
        self.original_writer = recorder_module.PointCloud
        self.original_reader = reader_module.PointCloud
        recorder_module.PointCloud = FakeWriterCloud
        reader_module.PointCloud = FakeReaderCloud
        FakeWriterCloud.calls.clear()

    def tearDown(self):
        recorder_module.PointCloud = self.original_writer
        reader_module.PointCloud = self.original_reader

    @staticmethod
    def point():
        return RadarPoint(
            cluster_id=3,
            dist_long=25.5,
            dist_latitude=-1.25,
            velocity_longitude=4.0,
            velocity_latitude=0.0,
            dynamic_property=2,
            rcs=-8.5,
            pdh=3,
            ambiguity_state=4,
            invalid_flag=7,
        )

    def test_camera_snapshot_is_grouped_with_point_cloud(self):
        recorded_at = datetime(2026, 7, 29, 12, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as folder:
            session = recorder_module.RadarRecordingSession()
            paths = session.start(folder, (1,))
            self.assertTrue(session.submit(1, (self.point(),), recorded_at))
            self.assertTrue(
                session.add_camera_snapshot(
                    1,
                    "camera_000001.jpg",
                    recorded_at.isoformat(),
                )
            )
            session.stop()
            metadata = json.loads(
                (Path(paths[1]) / recorder_module.RECORDING_METADATA_NAME).read_text()
            )
            timestamps = json.loads(
                (Path(paths[1]) / recorder_module.TIMESTAMPS_METADATA_NAME).read_text()
            )

        self.assertEqual(len(metadata), 1)
        self.assertEqual(metadata[0]["point_cloud"], "frame_000001.pcd")
        self.assertEqual(metadata[0]["frame_type"], "cluster")
        self.assertEqual(metadata[0]["camera_frame"], "camera_000001.jpg")
        self.assertEqual(timestamps["frame_000001.pcd"], recorded_at.isoformat(timespec="microseconds"))

    def test_point_cloud_reader_distinguishes_cluster_schema(self):
        FakeReaderCloud.current = FakeReaderCloud(
            recorder_module.CLUSTER_PCD_FIELDS,
            [[3, 25.5, -1.25, 4.0, 0.0, 2, -8.5, 3, 4, 7]],
        )
        with TemporaryDirectory() as folder:
            path = Path(folder) / "frame.pcd"
            path.write_bytes(b"pcd")
            reader = reader_module.PointCloudReader(path)

        self.assertEqual(reader.frame_type, "cluster")
        self.assertEqual(reader.clusters[0].cluster_id, 3)
        self.assertEqual(reader.objects, ())

    def test_point_cloud_reader_restores_extended_object_fields(self):
        values = [[
            8, 40.0, -2.0, 3.5, 0.25, 1, -6.0,
            np.nan, 0.063, 0.105, 0.288, np.nan, 2.187, 180.0,
            MISSING_QUALITY, 7,
            1.25, -0.25, 4, 12.0, 4.2, 1.8, 0x81,
        ]]
        FakeReaderCloud.current = FakeReaderCloud(recorder_module.OBJECT_PCD_FIELDS, values)
        with TemporaryDirectory() as folder:
            path = Path(folder) / "frame.pcd"
            path.write_bytes(b"pcd")
            reader = reader_module.PointCloudReader(path)

        obj = reader.objects[0]
        self.assertEqual(reader.frame_type, "object")
        self.assertIsNone(obj.dist_long_rms)
        self.assertIsNone(obj.acceleration_latitude_rms)
        self.assertIsNone(obj.measurement_state)
        self.assertEqual(obj.probability_of_existence, 7)
        self.assertAlmostEqual(obj.acceleration_longitude, 1.25)
        self.assertEqual(obj.object_class, 4)
        self.assertEqual(obj.collision_detection_regions, 0x81)

    def test_point_cloud_reader_supports_legacy_object_schema(self):
        values = [[
            8, 40.0, -2.0, 3.5, 0.25, 1, -6.0,
            np.nan, 0.063, 0.105, 0.288, np.nan, 2.187, 180.0,
            MISSING_QUALITY, 7,
        ]]
        FakeReaderCloud.current = FakeReaderCloud(
            recorder_module.LEGACY_OBJECT_PCD_FIELDS,
            values,
        )
        with TemporaryDirectory() as folder:
            path = Path(folder) / "legacy.pcd"
            path.write_bytes(b"pcd")
            reader = reader_module.PointCloudReader(path)

        obj = reader.objects[0]
        self.assertEqual(reader.frame_type, "object")
        self.assertIsNone(obj.measurement_state)
        self.assertEqual(obj.probability_of_existence, 7)
        self.assertIsNone(obj.object_class)
        self.assertIsNone(obj.collision_detection_regions)

    def test_camera_recorder_throttles_to_four_frames_per_second(self):
        original_imwrite = camera_module.cv.imwrite
        saved = []

        def fake_imwrite(path, _frame):
            Path(path).write_bytes(b"jpg")
            return True

        camera_module.cv.imwrite = fake_imwrite
        try:
            with TemporaryDirectory() as folder:
                recorder = camera_module.CameraSnapshotRecorder(saved.append)
                recorder.start({1: folder})
                frame = np.zeros((2, 2, 3), dtype=np.uint8)
                self.assertTrue(recorder.submit(frame, monotonic_time=1.0))
                self.assertFalse(recorder.submit(frame, monotonic_time=1.1))
                self.assertTrue(recorder.submit(frame, monotonic_time=1.25))
                count = recorder.stop()
                files = sorted(Path(folder).glob("camera_*.jpg"))
        finally:
            camera_module.cv.imwrite = original_imwrite

        self.assertEqual(count, 2)
        self.assertEqual(len(files), 2)
        self.assertEqual(len(saved), 2)

    def test_calibration_camera_recording_writes_adjusted_timestamp_manifest(self):
        original_imwrite = camera_module.cv.imwrite

        def fake_imwrite(path, _frame):
            Path(path).write_bytes(b"jpg")
            return True

        camera_module.cv.imwrite = fake_imwrite
        try:
            with TemporaryDirectory() as folder:
                recorder = camera_module.CameraSnapshotRecorder()
                recorder.start(
                    {4: folder},
                    calibration=True,
                    latency_adjustment_ms=250,
                )
                captured_at = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
                recorder.submit(
                    np.zeros((2, 2, 3), dtype=np.uint8),
                    captured_at=captured_at,
                    monotonic_time=1.0,
                )
                recorder.stop()
                metadata = json.loads(
                    (Path(folder) / camera_module.CAMERA_TIMESTAMPS_NAME).read_text()
                )
        finally:
            camera_module.cv.imwrite = original_imwrite

        self.assertEqual(metadata[0]["camera_frame"], "camera_000001.jpg")
        self.assertEqual(metadata[0]["captured_at"], captured_at.isoformat(timespec="microseconds"))
        self.assertEqual(metadata[0]["captured_at_unix"], 1787745600.0)
        self.assertEqual(
            metadata[0]["adjusted_at"],
            "2026-08-26T11:59:59.750000+00:00",
        )
        self.assertEqual(metadata[0]["adjusted_at_unix"], 1787745599.75)
        self.assertEqual(metadata[0]["latency_adjustment_ms"], 250.0)

    def test_camera_recording_interval_can_be_changed_to_thirty_fps(self):
        recorder = camera_module.CameraSnapshotRecorder()
        recorder.active = True
        recorder._queue = queue.Queue()
        recorder.set_snapshot_interval_seconds(1 / 30)
        frame = np.zeros((2, 2, 3), dtype=np.uint8)

        self.assertTrue(recorder.submit(frame, monotonic_time=1.0))
        self.assertFalse(recorder.submit(frame, monotonic_time=1.02))
        self.assertTrue(recorder.submit(frame, monotonic_time=1.034))

    def test_playback_loader_supports_new_and_legacy_metadata(self):
        timestamp = "2026-07-29T12:00:00+00:00"
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "frame_000001.pcd").write_bytes(b"pcd")
            (root / "camera_000001.jpg").write_bytes(b"jpg")
            (root / recorder_module.RECORDING_METADATA_NAME).write_text(json.dumps([{
                "point_cloud": "frame_000001.pcd",
                "recorded_at": timestamp,
                "frame_type": "cluster",
                "camera_frame": "camera_000001.jpg",
                "camera_recorded_at": timestamp,
            }]))
            entries = load_recording_entries(root)
            self.assertEqual(entries[0].camera_frame.name, "camera_000001.jpg")

            (root / recorder_module.RECORDING_METADATA_NAME).unlink()
            (root / recorder_module.TIMESTAMPS_METADATA_NAME).write_text(
                json.dumps({"frame_000001.pcd": timestamp})
            )
            legacy_entries = load_recording_entries(root)
            self.assertIsNone(legacy_entries[0].camera_frame)


class RequestedControlChangesTests(unittest.TestCase):
    class FakePipe:
        def __init__(self):
            self.sent = []

        def send(self, message):
            self.sent.append(message)

    class FakeConfig:
        connected_radar = False
        connected_cam = True

        def __init__(self):
            self.pending = False

        def set_recording_pending(self, starting):
            self.pending = starting

    def test_record_groups_default_to_unchecked_and_transport_controls_exist(self):
        layout = Configurations._create_record_layout()
        elements = {
            element.Key: element
            for row in layout
            for element in row
            if getattr(element, "Key", None)
        }

        for channel in range(1, 4):
            self.assertFalse(elements[f"record_radar_{channel}"].InitialState)
        self.assertEqual(elements["playback_toggle"].ButtonText, "START")
        self.assertEqual(elements["playback_stop"].ButtonText, "STOP")
        self.assertEqual(elements["playback_restart"].ButtonText, "RESTART")
        self.assertEqual(elements["playback_previous_5s"].ButtonText, "-5 s")
        self.assertEqual(elements["playback_next_5s"].ButtonText, "+5 s")

    def test_partial_recording_requires_confirmation(self):
        config = self.FakeConfig()
        pipe = self.FakePipe()
        with TemporaryDirectory() as folder:
            values = {"record_folder": folder, "record_radar_1": True}
            with patch.object(MAIN_BASE.sg, "popup_ok_cancel", return_value="Cancel"):
                MAIN_BASE._start_recording(values, config, pipe)
            self.assertFalse(config.pending)
            self.assertEqual(pipe.sent, [])

            with patch.object(MAIN_BASE.sg, "popup_ok_cancel", return_value="OK"):
                MAIN_BASE._start_recording(values, config, pipe)

        self.assertTrue(config.pending)
        self.assertEqual(pipe.sent[0][0], "record_start")

    def test_graph_resolution_and_ranges_can_be_changed(self):
        graph = Graph_radar(20, width=640, height=480, x_range=30, y_range=50)
        self.assertEqual(graph.base_image.shape, (480, 640, 3))
        self.assertEqual(graph.graph_to_pixel(-30, 0)[0], graph.margin)
        self.assertEqual(graph.graph_to_pixel(30, 0)[0], 640 - graph.margin)

        graph.set_resolution(900, 700)
        graph.set_range(40, 80)
        self.assertEqual(graph.base_image.shape, (700, 900, 3))
        self.assertEqual((graph.x_range, graph.y_range), (40.0, 80.0))

    def test_playback_restart_and_five_second_seek_choose_expected_frames(self):
        controller = PlaybackController.__new__(PlaybackController)
        timestamps = [0.0, 3.0, 6.0, 9.0]

        controller.transport_request = ("seek", 5.0)
        self.assertEqual(controller._apply_transport_request(1, timestamps), 3)
        controller.transport_request = ("seek", -5.0)
        self.assertEqual(controller._apply_transport_request(1, timestamps), 0)
        controller.transport_request = ("restart", 0.0)
        self.assertEqual(controller._apply_transport_request(3, timestamps), 0)


if __name__ == "__main__":
    unittest.main()
