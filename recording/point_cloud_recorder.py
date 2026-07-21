from datetime import datetime
import json
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Iterable

import numpy as np

from connection.connection_packages import MISSING_QUALITY, RadarObject, RadarPoint

try:
    from pypcd4 import PointCloud
except ImportError:  # Reported through the GUI when recording starts.
    PointCloud = None


CLUSTER_PCD_FIELDS = (
    "ID",
    "dist_long",
    "dist_latitude",
    "velocity_longitude",
    "velocity_latitude",
    "dynamic_property",
    "rcs",
    "pdh",
    "ambiguity_state",
    "invalid_flag",
)
CLUSTER_PCD_TYPES = (
    np.uint32,
    np.float32,
    np.float32,
    np.float32,
    np.float32,
    np.uint32,
    np.float32,
    np.uint32,
    np.uint32,
    np.uint32,
)
OBJECT_PCD_FIELDS = (
    "ID",
    "dist_long",
    "dist_latitude",
    "velocity_longitude",
    "velocity_latitude",
    "dynamic_property",
    "rcs",
    "dist_long_rms",
    "velocity_longitude_rms",
    "dist_latitude_rms",
    "velocity_latitude_rms",
    "acceleration_latitude_rms",
    "acceleration_longitude_rms",
    "orientation_rms",
    "measurement_state",
    "probability_of_existence",
)
OBJECT_PCD_TYPES = (
    np.uint32,
    np.float32,
    np.float32,
    np.float32,
    np.float32,
    np.uint32,
    np.float32,
    np.float32,
    np.float32,
    np.float32,
    np.float32,
    np.float32,
    np.float32,
    np.float32,
    np.uint32,
    np.uint32,
)

# Backwards-compatible names for code that expects the cluster schema.
PCD_FIELDS = CLUSTER_PCD_FIELDS
PCD_TYPES = CLUSTER_PCD_TYPES
RADAR_LETTERS = {1: "A", 2: "B", 3: "C"}
METADATA_FLUSH_SECONDS = 1.0
_STOP = object()


def _float_or_nan(value: float | None) -> float:
    return np.nan if value is None else value


def _int_or_missing(value: int | None) -> int:
    return MISSING_QUALITY if value is None else value


class PointCloudRecorder:
    def __init__(
        self,
        root: Path,
        channel: int,
        timestamp: str,
        progress_callback: Callable[[int, int], None] | None = None,
        queue_size: int = 64,
    ):
        if PointCloud is None:
            raise RuntimeError(
                "pypcd4 is required to record point clouds; install it with "
                "python -m pip install pypcd4"
            )
        if channel not in RADAR_LETTERS:
            raise ValueError(f"Unsupported radar channel: {channel}")

        self.channel = channel
        self.folder = root / f"recording_{RADAR_LETTERS[channel]}_{timestamp}"
        self.folder.mkdir(parents=True, exist_ok=False)
        self.timestamps_path = self.folder / "timestamps.json"
        self.progress_callback = progress_callback
        self.frames_written = 0
        self.error: Exception | None = None
        self._timestamps: dict[str, str] = {}
        self._metadata_dirty = False
        self._last_metadata_flush = time.monotonic()
        self._write_timestamps()
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(
            target=self._write_loop,
            name=f"pcd-writer-{RADAR_LETTERS[channel]}",
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        points: Iterable[RadarPoint | RadarObject],
        recorded_at: datetime,
        frame_type: str = "cluster",
    ) -> bool:
        if self.error is not None:
            return False
        if frame_type not in ("cluster", "object"):
            self.error = ValueError(f"Unsupported radar frame type: {frame_type}")
            return False
        try:
            timestamp = recorded_at.isoformat(timespec="microseconds")
            self._queue.put_nowait((tuple(points), timestamp, frame_type))
            return True
        except queue.Full:
            self.error = RuntimeError(
                f"Recording queue for radar {RADAR_LETTERS[self.channel]} is full"
            )
            return False

    def stop(self) -> None:
        while self._thread.is_alive():
            try:
                self._queue.put(_STOP, timeout=0.1)
                break
            except queue.Full:
                continue
        self._thread.join()
        if self.error is not None:
            raise self.error

    def _write_loop(self) -> None:
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is _STOP:
                        self._flush_metadata(force=True)
                        return
                    points, recorded_at, frame_type = item
                    frame_number = self.frames_written + 1
                    path = self.folder / f"frame_{frame_number:06d}.pcd"
                    self._save(path, points, frame_type)
                    self.frames_written = frame_number
                    self._timestamps[path.name] = recorded_at
                    self._metadata_dirty = True
                    self._flush_metadata()
                    if self.progress_callback:
                        self.progress_callback(self.channel, self.frames_written)
                finally:
                    self._queue.task_done()
        except Exception as error:
            self.error = error
            try:
                self._flush_metadata(force=True)
            except Exception:
                pass

    def _flush_metadata(self, *, force: bool = False) -> None:
        if not self._metadata_dirty:
            return
        now = time.monotonic()
        if not force and now - self._last_metadata_flush < METADATA_FLUSH_SECONDS:
            return
        self._write_timestamps()
        self._metadata_dirty = False
        self._last_metadata_flush = now

    def _write_timestamps(self) -> None:
        temporary_path = self.timestamps_path.with_suffix(".json.tmp")
        temporary_path.write_text(
            json.dumps(self._timestamps, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary_path.replace(self.timestamps_path)

    @staticmethod
    def _save(
        path: Path,
        points: tuple[RadarPoint | RadarObject, ...],
        frame_type: str,
    ) -> None:
        if frame_type == "cluster":
            fields, types = CLUSTER_PCD_FIELDS, CLUSTER_PCD_TYPES
            values = np.empty((len(points), len(fields)), dtype=object)
            for row, point in enumerate(points):
                values[row] = (
                    point.cluster_id,
                    point.dist_long,
                    point.dist_latitude,
                    point.velocity_longitude,
                    point.velocity_latitude,
                    point.dynamic_property,
                    point.rcs,
                    point.pdh,
                    point.ambiguity_state,
                    point.invalid_flag,
                )
        elif frame_type == "object":
            fields, types = OBJECT_PCD_FIELDS, OBJECT_PCD_TYPES
            values = np.empty((len(points), len(fields)), dtype=object)
            for row, obj in enumerate(points):
                values[row] = (
                    obj.object_id,
                    obj.dist_long,
                    obj.dist_latitude,
                    obj.velocity_longitude,
                    obj.velocity_latitude,
                    obj.dynamic_property,
                    obj.rcs,
                    _float_or_nan(obj.dist_long_rms),
                    _float_or_nan(obj.velocity_longitude_rms),
                    _float_or_nan(obj.dist_latitude_rms),
                    _float_or_nan(obj.velocity_latitude_rms),
                    _float_or_nan(obj.acceleration_latitude_rms),
                    _float_or_nan(obj.acceleration_longitude_rms),
                    _float_or_nan(obj.orientation_rms),
                    _int_or_missing(obj.measurement_state),
                    _int_or_missing(obj.probability_of_existence),
                )
        else:
            raise ValueError(f"Unsupported radar frame type: {frame_type}")
        PointCloud.from_points(values, fields, types).save(str(path))


class RadarRecordingSession:
    def __init__(self, progress_callback: Callable[[int, int], None] | None = None):
        self.recorders: dict[int, PointCloudRecorder] = {}
        self.progress_callback = progress_callback

    @property
    def active(self) -> bool:
        return bool(self.recorders)

    @property
    def channels(self) -> frozenset[int]:
        return frozenset(self.recorders)

    def start(self, root: str, channels: Iterable[int]) -> dict[int, str]:
        if self.active:
            raise RuntimeError("A recording session is already active")

        root_path = Path(root).expanduser()
        if not root_path.is_dir():
            raise ValueError("The recording destination must be an existing folder")

        selected = sorted(set(channels))
        if not selected:
            raise ValueError("Select at least one radar to record")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        created: dict[int, PointCloudRecorder] = {}
        try:
            for channel in selected:
                created[channel] = PointCloudRecorder(
                    root_path,
                    channel,
                    timestamp,
                    self.progress_callback,
                )
        except Exception:
            for recorder in created.values():
                try:
                    recorder.stop()
                except Exception:
                    pass
            raise

        self.recorders = created
        return {channel: str(recorder.folder) for channel, recorder in created.items()}

    def submit(
        self,
        channel: int,
        points: Iterable[RadarPoint | RadarObject],
        recorded_at: datetime,
        frame_type: str = "cluster",
    ) -> bool:
        recorder = self.recorders.get(channel)
        return recorder.submit(points, recorded_at, frame_type) if recorder else False

    def poll_error(self) -> Exception | None:
        for recorder in self.recorders.values():
            if recorder.error is not None:
                return recorder.error
        return None

    def stop(self) -> dict[int, int]:
        recorders, self.recorders = self.recorders, {}
        first_error = None
        for recorder in recorders.values():
            try:
                recorder.stop()
            except Exception as error:
                first_error = first_error or error
        counts = {channel: recorder.frames_written for channel, recorder in recorders.items()}
        if first_error is not None:
            raise first_error
        return counts
