from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import queue
import threading
import time
from typing import Callable, Mapping

import cv2 as cv
import numpy as np


CAMERA_FRAME_RATE = 30
DEFAULT_RECORDED_FRAMES_PER_30 = 30
CAMERA_TIMESTAMPS_NAME = "camera_timestamps.json"
CAMERA_TIMESTAMPS_JOURNAL_NAME = "camera_timestamps.jsonl"
CAMERA_RECORDING_SUMMARY_NAME = "camera_recording_summary.json"
_STOP = object()


class CameraSnapshotRecorder:
    def __init__(
        self,
        saved_callback: Callable[[dict], None] | None = None,
        dropped_callback: Callable[[dict], None] | None = None,
        queue_size: int = 8,
    ):
        self.saved_callback = saved_callback
        self.dropped_callback = dropped_callback
        self.queue_size = queue_size
        self.error: Exception | None = None
        self.active = False
        self._folders: dict[int, Path] = {}
        self._queue: queue.Queue | None = None
        self._thread: threading.Thread | None = None
        self._frame_number = 0
        self._calibration_metadata_paths: tuple[Path, ...] = ()
        self._calibration_journal_paths: tuple[Path, ...] = ()
        self._calibration_summary_paths: tuple[Path, ...] = ()
        self._latency_adjustment_ms = 0.0
        self._calibration = False
        self.recorded_frames_per_30 = DEFAULT_RECORDED_FRAMES_PER_30
        self._sampling_accumulator = 0
        self.frames_observed = 0
        self.frames_selected = 0
        self.frames_dropped = 0
        self.pipeline_frames_missing = 0
        self.frames_rejected_invalid_timing = 0
        self._recording_started_at = ""
        self._recording_started_monotonic_ns = 0

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
        self._sampling_accumulator = CAMERA_FRAME_RATE - self.recorded_frames_per_30
        self._frame_number = 0
        self.frames_observed = 0
        self.frames_selected = 0
        self.frames_dropped = 0
        self.pipeline_frames_missing = 0
        self.frames_rejected_invalid_timing = 0
        self.error = None
        self._latency_adjustment_ms = float(latency_adjustment_ms)
        self._calibration = calibration
        self._calibration_metadata_paths = (
            tuple(folder / CAMERA_TIMESTAMPS_NAME for folder in selected.values())
            if calibration
            else ()
        )
        self._calibration_journal_paths = (
            tuple(
                folder / CAMERA_TIMESTAMPS_JOURNAL_NAME
                for folder in selected.values()
            )
            if calibration
            else ()
        )
        self._calibration_summary_paths = (
            tuple(
                folder / CAMERA_RECORDING_SUMMARY_NAME
                for folder in selected.values()
            )
            if calibration
            else ()
        )
        self._recording_started_at = datetime.now().astimezone().isoformat(
            timespec="microseconds"
        )
        self._recording_started_monotonic_ns = time.monotonic_ns()
        self._initialize_calibration_journal()
        self._write_calibration_metadata()
        self._write_calibration_summary()
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
        timing: Mapping | None = None,
    ) -> bool:
        if not self.active or self.error is not None or self._queue is None:
            return False
        self.frames_observed += 1
        timing = dict(timing or {})
        self.pipeline_frames_missing += int(
            timing.get("estimated_missing_frames", 0)
        )
        self._sampling_accumulator += self.recorded_frames_per_30
        if self._sampling_accumulator < CAMERA_FRAME_RATE:
            return False
        self._sampling_accumulator -= CAMERA_FRAME_RATE
        self.frames_selected += 1
        timestamp = (captured_at or datetime.now().astimezone()).isoformat(
            timespec="microseconds"
        )
        try:
            self._queue.put_nowait((
                frame,
                timestamp,
                timing,
            ))
        except queue.Full:
            self.frames_dropped += 1
            if self.dropped_callback is not None:
                self.dropped_callback({
                    "reason": "image writer queue is full",
                    "dropped": self.frames_dropped,
                    "selected": self.frames_selected,
                    "queue_size": self.queue_size,
                    "timing": timing,
                })
            return False
        return True

    def poll_error(self) -> Exception | None:
        return self.error

    def note_invalid_timing_frame(self) -> None:
        if self.active:
            self.frames_rejected_invalid_timing += 1

    def set_latency_adjustment_ms(self, value: float) -> None:
        self._latency_adjustment_ms = float(value)

    def set_recorded_frames_per_30(self, value: int) -> None:
        numeric_value = float(value)
        if not numeric_value.is_integer():
            raise ValueError("Recorded frames must be a whole number")
        frames_per_30 = int(numeric_value)
        if not 1 <= frames_per_30 <= CAMERA_FRAME_RATE:
            raise ValueError("Recorded frames must be between 1 and 30")
        self.recorded_frames_per_30 = frames_per_30
        self._sampling_accumulator = CAMERA_FRAME_RATE - frames_per_30

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
        self._write_calibration_metadata()
        self._write_calibration_summary(final=True)
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
                    frame, captured_at, timing = item
                    frame_number = self._frame_number + 1
                    filename = f"camera_{frame_number:06d}.jpg"
                    files = {}
                    for channel, folder in self._folders.items():
                        path = folder / filename
                        if not cv.imwrite(str(path), frame):
                            raise RuntimeError(f"Could not save camera snapshot: {path}")
                        files[channel] = filename
                    self._frame_number = frame_number
                    saved_at_ns = time.time_ns()
                    self._record_calibration_timestamp(
                        filename,
                        captured_at,
                        timing,
                        saved_at_ns,
                    )
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

    def _record_calibration_timestamp(
        self,
        filename: str,
        captured_at: str,
        timing: Mapping,
        saved_at_ns: int,
    ) -> None:
        if not self._calibration_metadata_paths:
            return
        timing = dict(timing)
        captured_datetime = datetime.fromisoformat(captured_at)
        attempted_corrected_at = captured_datetime - timedelta(
            milliseconds=self._latency_adjustment_ms,
        )
        camera_ntp_ns = timing.get("camera_ntp_ns")
        pts_time_ns = timing.get("pts_time_ns")
        host_received_ns = timing.get("host_realtime_received_ns")
        attempted_corrected_ns = (
            int(timing["attempted_capture_time_ns"])
            - round(self._latency_adjustment_ms * 1_000_000)
            if timing.get("attempted_capture_time_ns") is not None
            else round(attempted_corrected_at.timestamp() * 1_000_000_000)
        )
        timing.update({
            "latency_adjustment_ms": self._latency_adjustment_ms,
            "attempted_corrected_time_ns": attempted_corrected_ns,
        })
        record = {
            "camera_frame": filename,
            "pts_ns": timing.get("pts_ns"),
            "running_time_ns": timing.get("running_time_ns"),
            "pts_time_at": self._iso(self._datetime_from_ns(pts_time_ns)),
            "pts_time_unix_ns": pts_time_ns,
            "camera_ntp_at": self._iso(self._datetime_from_ns(camera_ntp_ns)),
            "camera_ntp_unix": (
                camera_ntp_ns / 1_000_000_000
                if camera_ntp_ns is not None
                else None
            ),
            "camera_ntp_unix_ns": camera_ntp_ns,
            "camera_ntp_status": timing.get("camera_ntp_status"),
            "camera_ntp_valid": timing.get("camera_ntp_valid"),
            "host_received_at": self._iso(
                self._datetime_from_ns(host_received_ns)
            ),
            "host_received_unix_ns": host_received_ns,
            "capture_time_at": self._iso(
                self._datetime_from_ns(host_received_ns)
            ),
            "capture_time_unix_ns": host_received_ns,
            "captured_at": captured_at,
            "captured_at_unix": captured_datetime.timestamp(),
            "captured_at_unix_ns": timing.get("attempted_capture_time_ns"),
            "attempted_corrected_at": self._iso(
                self._datetime_from_ns(attempted_corrected_ns)
            ),
            "attempted_corrected_unix": attempted_corrected_ns / 1_000_000_000,
            "attempted_corrected_unix_ns": attempted_corrected_ns,
            "adjusted_at": self._iso(
                self._datetime_from_ns(attempted_corrected_ns)
            ),
            "adjusted_at_unix": attempted_corrected_ns / 1_000_000_000,
            "latency_adjustment_ms": self._latency_adjustment_ms,
            "saved_at": self._iso(self._datetime_from_ns(saved_at_ns)),
            "saved_at_unix_ns": saved_at_ns,
            "timing": timing,
        }
        self._append_calibration_journal(record)

    @staticmethod
    def _datetime_from_ns(value) -> datetime | None:
        if value is None:
            return None
        seconds, nanoseconds = divmod(int(value), 1_000_000_000)
        try:
            return datetime.fromtimestamp(seconds, timezone.utc).replace(
                microsecond=nanoseconds // 1_000,
            )
        except (OverflowError, OSError, ValueError):
            return None

    @staticmethod
    def _iso(value: datetime | None) -> str | None:
        return value.isoformat(timespec="microseconds") if value is not None else None

    def _write_calibration_summary(self, *, final: bool = False) -> None:
        if not self._calibration_summary_paths:
            return
        summary = {
            "started_at": self._recording_started_at,
            "started_monotonic_ns": self._recording_started_monotonic_ns,
            "recorded_frames_per_30": self.recorded_frames_per_30,
            "frames_observed": self.frames_observed,
            "frames_selected": self.frames_selected,
            "frames_dropped_writer_queue": self.frames_dropped,
            "estimated_frames_missing_from_pts": self.pipeline_frames_missing,
            "frames_rejected_invalid_timing": self.frames_rejected_invalid_timing,
            "total_frames_lost": (
                self.frames_dropped
                + self.pipeline_frames_missing
                + self.frames_rejected_invalid_timing
            ),
            "frames_saved": self._frame_number,
        }
        if final:
            summary["stopped_at"] = datetime.now().astimezone().isoformat(
                timespec="microseconds"
            )
        for path in self._calibration_summary_paths:
            temporary_path = path.with_suffix(path.suffix + ".tmp")
            temporary_path.write_text(
                json.dumps(summary, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary_path.replace(path)

    def _initialize_calibration_journal(self) -> None:
        for path in self._calibration_journal_paths:
            path.write_text("", encoding="utf-8")

    def _append_calibration_journal(self, record: Mapping) -> None:
        serialized = json.dumps(record, separators=(",", ":")) + "\n"
        for path in self._calibration_journal_paths:
            with path.open("a", encoding="utf-8") as journal:
                journal.write(serialized)

    def _write_calibration_metadata(self) -> None:
        for path, journal_path in zip(
            self._calibration_metadata_paths,
            self._calibration_journal_paths,
        ):
            temporary_path = path.with_suffix(path.suffix + ".tmp")
            with (
                journal_path.open("r", encoding="utf-8") as journal,
                temporary_path.open("w", encoding="utf-8") as manifest,
            ):
                manifest.write("[\n")
                first_record = True
                for line in journal:
                    serialized = line.strip()
                    if not serialized:
                        continue
                    if not first_record:
                        manifest.write(",\n")
                    manifest.write(f"  {serialized}")
                    first_record = False
                manifest.write("\n]\n" if not first_record else "]\n")
            temporary_path.replace(path)
