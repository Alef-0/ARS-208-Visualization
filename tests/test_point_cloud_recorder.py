from datetime import datetime, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np

from connection.connection_packages import MISSING_QUALITY, RadarPoint
import recording.point_cloud_recorder as recorder_module


class FakePointCloud:
    calls = []

    def __init__(self, values, fields, types):
        self.values = values
        self.fields = fields
        self.types = types

    @classmethod
    def from_points(cls, values, fields, types):
        cloud = cls(values.copy(), tuple(fields), tuple(types))
        cls.calls.append(cloud)
        return cloud

    def save(self, path):
        self.path = Path(path)
        self.path.write_bytes(b"pcd")


class PointCloudRecorderTests(unittest.TestCase):
    def setUp(self):
        self.original_point_cloud = recorder_module.PointCloud
        recorder_module.PointCloud = FakePointCloud
        FakePointCloud.calls.clear()

    def tearDown(self):
        recorder_module.PointCloud = self.original_point_cloud

    @staticmethod
    def point(cluster_id=3):
        return RadarPoint(
            cluster_id=cluster_id,
            dist_long=25.5,
            dist_latitude=-1.25,
            velocity_longitude=4.0,
            velocity_latitude=0.0,
            dynamic_property=2,
            rcs=-8.5,
            pdh=MISSING_QUALITY,
            ambiguity_state=4,
            invalid_flag=7,
        )

    def test_session_creates_one_timestamped_folder_per_radar(self):
        with TemporaryDirectory() as folder:
            session = recorder_module.RadarRecordingSession()
            paths = session.start(folder, (1, 3))
            counts = session.stop()

        name_a = Path(paths[1]).name
        name_c = Path(paths[3]).name
        self.assertTrue(name_a.startswith("recording_A_"))
        self.assertTrue(name_c.startswith("recording_C_"))
        self.assertEqual(name_a.removeprefix("recording_A_"), name_c.removeprefix("recording_C_"))
        self.assertEqual(counts, {1: 0, 3: 0})

    def test_stop_flushes_frames_and_preserves_field_order(self):
        recorded_at = datetime(2026, 7, 21, 18, 30, 45, 123456, tzinfo=timezone.utc)
        with TemporaryDirectory() as folder:
            session = recorder_module.RadarRecordingSession()
            paths = session.start(folder, (2,))
            self.assertTrue(session.submit(2, (self.point(),), recorded_at))
            counts = session.stop()

            recording_folder = Path(paths[2])
            frame_path = recording_folder / "frame_000001.pcd"
            self.assertTrue(frame_path.is_file())
            timestamps = json.loads((recording_folder / "timestamps.json").read_text())

        self.assertEqual(counts, {2: 1})
        self.assertEqual(
            timestamps,
            {"frame_000001.pcd": recorded_at.isoformat(timespec="microseconds")},
        )
        cloud, = FakePointCloud.calls
        self.assertEqual(cloud.fields, recorder_module.PCD_FIELDS)
        self.assertTrue(all(np.dtype(dtype).itemsize == 4 for dtype in cloud.types))
        self.assertEqual(tuple(cloud.values[0]), (
            3, 25.5, -1.25, 4.0, 0.0, 2, -8.5,
            MISSING_QUALITY, 4, 7,
        ))

    def test_empty_radar_frame_is_stored(self):
        recorded_at = datetime.now(timezone.utc)
        with TemporaryDirectory() as folder:
            session = recorder_module.RadarRecordingSession()
            paths = session.start(folder, (1,))
            self.assertTrue(session.submit(1, (), recorded_at))
            counts = session.stop()

            recording_folder = Path(paths[1])
            self.assertTrue((recording_folder / "frame_000001.pcd").is_file())
            timestamps = json.loads((recording_folder / "timestamps.json").read_text())

        self.assertEqual(counts, {1: 1})
        self.assertEqual(list(timestamps), ["frame_000001.pcd"])
        self.assertEqual(FakePointCloud.calls[0].values.shape, (0, 10))

    def test_timestamp_file_exists_before_first_frame(self):
        with TemporaryDirectory() as folder:
            session = recorder_module.RadarRecordingSession()
            paths = session.start(folder, (3,))
            timestamp_path = Path(paths[3]) / "timestamps.json"
            self.assertEqual(json.loads(timestamp_path.read_text()), {})
            session.stop()


if __name__ == "__main__":
    unittest.main()
