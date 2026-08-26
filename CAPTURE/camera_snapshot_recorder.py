from datetime import datetime, timedelta
import json
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Mapping

import cv2 as cv
import numpy as np


SNAPSHOT_INTERVAL_SECONDS = 0.25
CAMERA_TIMESTAMPS_NAME = "camera_timestamps.json"
_STOP = object()


class CameraSnapshotRecorder:
    def __init__(
        self,
        saved_callback: Callable[[dict], None] | None = None,
        queue_size: int = 8,
    ):
        self.saved_callback = saved_callback
        self.queue_size = queue_size
        self.error: Exception | None = None
        self.active = False
        self._folders: dict[int, Path] = {}
        self._queue: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self._last_snapshot_time: float | None = None
        self._frame_number = 0
        self._calibration_metadata_paths: tuple[Path, ...] = ()
        self._calibration_records: list[dict] = []
        self._latency_adjustment_ms = 0.0
        self._calibration = False

    def start(
        self,
        folders: Mapping[int, str],
        *,
        calibration: bool = False,
        latency_adjustment_ms: float = 0.0,
    ) -> None:
        if self.active:
            raise RuntimeError("Camera snapshot recording is already active")
        selected = {int(channel): Path(folder).expanduser() for channel, folder in folders.items()}
        if not selected:
            raise ValueError("No radar recording folders were supplied")
        for folder in selected.values():
            if not folder.is_dir():
                raise ValueError(f"Recording folder does not exist: {folder}")

        self._folders = selected
        self._queue = queue.Queue(maxsize=self.queue_size)
        self._last_snapshot_time = None
        self._frame_number = 0
        self.error = None
        self._calibration_records = []
        self._latency_adjustment_ms = float(latency_adjustment_ms)
        self._calibration = calibration
        self._calibration_metadata_paths = (
            tuple(folder / CAMERA_TIMESTAMPS_NAME for folder in selected.values())
            if calibration
            else ()
        )
        self._write_calibration_metadata()
        self.active = True
        self._thread = threading.Thread(
            target=self._write_loop,
            name="camera-snapshot-writer",
            daemon=True,
        )
        self._thread.start()

    def submit(
        self,
        frame: np.ndarray,
        captured_at: datetime | None = None,
        monotonic_time: float | None = None,
    ) -> bool:
        if not self.active or self.error is not None or self._queue is None:
            return False
        now = time.monotonic() if monotonic_time is None else monotonic_time
        if (
            self._last_snapshot_time is not None
            and now - self._last_snapshot_time < SNAPSHOT_INTERVAL_SECONDS
        ):
            return False
        timestamp = (captured_at or datetime.now().astimezone()).isoformat(
            timespec="microseconds"
        )
        try:
            self._queue.put_nowait((frame.copy(), timestamp))
        except queue.Full:
            return False
        self._last_snapshot_time = now
        return True

    def poll_error(self) -> Exception | None:
        return self.error

    def set_latency_adjustment_ms(self, value: float) -> None:
        self._latency_adjustment_ms = float(value)

    def stop(self) -> int:
        if not self.active:
            return self._frame_number
        self.active = False
        if self._queue is not None and self._thread is not None:
            while self._thread.is_alive():
                try:
                    self._queue.put(_STOP, timeout=0.1)
                    break
                except queue.Full:
                    continue
            self._thread.join()
        if self.error is not None:
            raise self.error
        return self._frame_number

    def _write_loop(self) -> None:
        assert self._queue is not None
        try:
            while True:
                item = self._queue.get()
                try:
                    if item is _STOP:
                        return
                    frame, captured_at = item
                    self._frame_number += 1
                    filename = f"camera_{self._frame_number:06d}.jpg"
                    files = {}
                    for channel, folder in self._folders.items():
                        path = folder / filename
                        if not cv.imwrite(str(path), frame):
                            raise RuntimeError(f"Could not save camera snapshot: {path}")
                        files[channel] = filename
                    self._record_calibration_timestamp(filename, captured_at)
                    if self.saved_callback:
                        self.saved_callback({
                            "files": files,
                            "captured_at": captured_at,
                            "calibration": self._calibration,
                        })
                finally:
                    self._queue.task_done()
        except Exception as error:
            self.error = error

    def _record_calibration_timestamp(self, filename: str, captured_at: str) -> None:
        if not self._calibration_metadata_paths:
            return
        adjusted_at = datetime.fromisoformat(captured_at) - timedelta(
            milliseconds=self._latency_adjustment_ms
        )
        self._calibration_records.append({
            "camera_frame": filename,
            "captured_at": captured_at,
            "adjusted_at": adjusted_at.isoformat(timespec="microseconds"),
            "latency_adjustment_ms": self._latency_adjustment_ms,
        })
        self._write_calibration_metadata()

    def _write_calibration_metadata(self) -> None:
        for path in self._calibration_metadata_paths:
            temporary_path = path.with_suffix(path.suffix + ".tmp")
            temporary_path.write_text(
                json.dumps(self._calibration_records, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(path)
