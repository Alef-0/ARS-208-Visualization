from datetime import datetime
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Mapping

import cv2 as cv
import numpy as np


SNAPSHOT_INTERVAL_SECONDS = 0.25
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

    def start(self, folders: Mapping[int, str]) -> None:
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
                    if self.saved_callback:
                        self.saved_callback({
                            "files": files,
                            "captured_at": captured_at,
                        })
                finally:
                    self._queue.task_done()
        except Exception as error:
            self.error = error
