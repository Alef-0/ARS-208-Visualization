from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import signal
import time

import cv2 as cv

from GRAPH.graph_draw import Graph_radar
from GRAPH.graph_filter import Filter_graph
from CAPTURE.point_cloud_reader import PointCloudReader
from CAPTURE.point_cloud_recorder import RECORDING_METADATA_NAME, TIMESTAMPS_METADATA_NAME

DEFAULT_PLAYBACK_WIDTH = 1280
DEFAULT_PLAYBACK_HEIGHT = 720


@dataclass(frozen=True)
class PlaybackEntry:
    point_cloud: Path
    recorded_at: datetime
    camera_frame: Path | None = None


def load_recording_entries(folder: str | Path) -> tuple[PlaybackEntry, ...]:
    root = Path(folder).expanduser()
    if not root.is_dir():
        raise ValueError("The playback source must be an existing recording folder")
    metadata_path = root / RECORDING_METADATA_NAME
    timestamps_path = root / TIMESTAMPS_METADATA_NAME
    entries = []
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, list):
            raise ValueError(f"Invalid {RECORDING_METADATA_NAME} format")
        for item in metadata:
            camera_name = item.get("camera_frame")
            entries.append(PlaybackEntry(
                point_cloud=root / item["point_cloud"],
                recorded_at=datetime.fromisoformat(item["recorded_at"]),
                camera_frame=root / camera_name if camera_name else None,
            ))
    elif timestamps_path.is_file():
        timestamps = json.loads(timestamps_path.read_text(encoding="utf-8"))
        if not isinstance(timestamps, dict):
            raise ValueError(f"Invalid {TIMESTAMPS_METADATA_NAME} format")
        for filename, recorded_at in timestamps.items():
            entries.append(PlaybackEntry(root / filename, datetime.fromisoformat(recorded_at)))
    else:
        raise ValueError(
            f"The folder does not contain {RECORDING_METADATA_NAME} or {TIMESTAMPS_METADATA_NAME}"
        )
    entries.sort(key=lambda entry: entry.recorded_at)
    if not entries:
        raise ValueError("The recording folder contains no point-cloud frames")
    missing = [entry.point_cloud.name for entry in entries if not entry.point_cloud.is_file()]
    if missing:
        raise FileNotFoundError(f"Missing point-cloud file: {missing[0]}")
    return tuple(entries)


def _put_status(pool, message, payload):
    try:
        pool.put((message, payload), timeout=0.2)
    except Exception:
        pass


def _destroy_window(name):
    try:
        cv.destroyWindow(name)
        cv.waitKey(1)
    except cv.error:
        pass


class PlaybackController:
    def __init__(self, connection, pool, shutdown_event, initial_values):
        self.connection = connection
        self.pool = pool
        self.shutdown_event = shutdown_event
        self.filters = Filter_graph(initial_values)
        self.graph = Graph_radar(initial_values.get("point_cutoff", 15.0))
        self.stop_requested = False
        self.width = DEFAULT_PLAYBACK_WIDTH
        self.height = DEFAULT_PLAYBACK_HEIGHT

    def run(self):
        while not self.shutdown_event.is_set():
            if not self.connection.poll(0.05):
                continue
            try:
                event, value = self.connection.recv()
            except (EOFError, OSError):
                self.shutdown_event.set()
                break
            if event == "STOP":
                self.shutdown_event.set()
            elif event == "playback_start":
                self._play(value)
            elif event == "playback_resolution":
                self._set_resolution(value)
            elif event == "point_cutoff":
                self.graph.set_distance_cutoff(value.get("distance", 15.0))
            elif isinstance(event, str) and event.startswith("filter"):
                self.filters.update_values(event, value)
        self._close_windows()

    def _set_resolution(self, value):
        width = int(value.get("width", DEFAULT_PLAYBACK_WIDTH))
        height = int(value.get("height", DEFAULT_PLAYBACK_HEIGHT))
        if width <= 0 or height <= 0:
            raise ValueError("Playback image dimensions must be positive")
        self.width, self.height = width, height

    def _play(self, value):
        folder = value["folder"] if isinstance(value, dict) else value
        if isinstance(value, dict):
            self._set_resolution(value)
        try:
            entries = load_recording_entries(folder)
            self.stop_requested = False
            _put_status(self.pool, "playback_state", {
                "active": True, "folder": str(Path(folder).expanduser()),
                "current": 0, "total": len(entries), "mode": "record",
            })
            for index, entry in enumerate(entries, start=1):
                self._process_controls()
                if self.stop_requested or self.shutdown_event.is_set():
                    break
                reader = PointCloudReader(entry.point_cloud)
                if reader.frame_type == "cluster":
                    x, y, colors = self.filters.filter_point_sequence(reader.clusters)
                else:
                    x, y, colors = self.filters.filter_object_sequence(reader.objects)
                self.graph.show_points(x, y, colors, self.filters.last_points)
                if entry.camera_frame and entry.camera_frame.is_file():
                    image = cv.imread(str(entry.camera_frame))
                    if image is not None:
                        image = cv.resize(image, (self.width, self.height), interpolation=cv.INTER_AREA)
                        cv.imshow("CAMERA PLAYBACK", image)
                        cv.waitKey(1)
                _put_status(self.pool, "playback_progress", {
                    "current": index, "total": len(entries),
                    "file": entry.point_cloud.name, "mode": "record",
                })
                if index < len(entries):
                    delay = max(0.0, (entries[index].recorded_at - entry.recorded_at).total_seconds())
                    self._wait(delay)
            completed = not self.stop_requested and not self.shutdown_event.is_set()
            _put_status(self.pool, "playback_state", {
                "active": False, "completed": completed,
                "current": len(entries) if completed else 0,
                "total": len(entries), "mode": "record",
            })
        except Exception as error:
            _put_status(self.pool, "playback_error", {"mode": "record", "message": str(error)})
            _put_status(self.pool, "playback_state", {
                "active": False, "completed": False, "current": 0, "total": 0, "mode": "record",
            })
        finally:
            self._close_windows()

    def _handle_control(self, event, value):
        if event == "STOP":
            self.shutdown_event.set()
        elif event == "playback_stop":
            self.stop_requested = True
        elif event == "playback_resolution":
            self._set_resolution(value)
        elif event == "point_cutoff":
            self.graph.set_distance_cutoff(value.get("distance", 15.0))
        elif isinstance(event, str) and event.startswith("filter"):
            self.filters.update_values(event, value)

    def _process_controls(self):
        while self.connection.poll():
            try:
                event, value = self.connection.recv()
            except (EOFError, OSError):
                self.shutdown_event.set()
                return
            self._handle_control(event, value)

    def _wait(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self.shutdown_event.is_set() or self.stop_requested:
                return
            remaining = max(0.0, deadline - time.monotonic())
            if not self.connection.poll(min(0.05, remaining)):
                continue
            try:
                event, value = self.connection.recv()
            except (EOFError, OSError):
                self.shutdown_event.set()
                return
            self._handle_control(event, value)

    def _close_windows(self):
        _destroy_window("RADAR")
        _destroy_window("CAMERA PLAYBACK")


def playback_main(connection, pool, shutdown_event, initial_values):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())
    PlaybackController(connection, pool, shutdown_event, initial_values).run()
