from datetime import datetime
from pathlib import Path
import queue
import threading
from typing import Callable, Iterable

import numpy as np

from connection.connection_packages import RadarPoint

try:
    from pypcd4 import PointCloud
except ImportError:  # Reported through the GUI when recording starts.
    PointCloud = None


PCD_FIELDS = (
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
PCD_TYPES = (
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
RADAR_LETTERS = {1: "A", 2: "B", 3: "C"}
_STOP = object()


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
        self.progress_callback = progress_callback
        self.frames_written = 0
        self.error: Exception | None = None
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._thread = threading.Thread(
            target=self._write_loop,
            name=f"pcd-writer-{RADAR_LETTERS[channel]}",
            daemon=True,
        )
        self._thread.start()

    def submit(self, points: Iterable[RadarPoint]) -> bool:
        if self.error is not None:
            return False
        try:
            self._queue.put_nowait(tuple(points))
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
                points = self._queue.get()
                try:
                    if points is _STOP:
                        return
                    frame_number = self.frames_written + 1
                    path = self.folder / f"frame_{frame_number:06d}.pcd"
                    self._save(path, points)
                    self.frames_written = frame_number
                    if self.progress_callback:
                        self.progress_callback(self.channel, self.frames_written)
                finally:
                    self._queue.task_done()
        except Exception as error:
            self.error = error

    @staticmethod
    def _save(path: Path, points: tuple[RadarPoint, ...]) -> None:
        values = np.empty((len(points), len(PCD_FIELDS)), dtype=object)
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
        PointCloud.from_points(values, PCD_FIELDS, PCD_TYPES).save(str(path))


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

    def submit(self, channel: int, points: Iterable[RadarPoint]) -> bool:
        recorder = self.recorders.get(channel)
        return recorder.submit(points) if recorder else False

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
