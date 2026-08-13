from datetime import datetime, timedelta
import json
from pathlib import Path
import signal
import time

import cv2 as cv

from GRAPH.graph_draw import Graph_radar
from GRAPH.graph_filter import Filter_graph
from CAPTURE.manual_snapshot import ManualSnapshotWriter
from CAPTURE.point_cloud_reader import PointCloudReader
from CAPTURE.point_cloud_recorder import CAMERA_DELAY_SECONDS, RECORDING_METADATA_NAME

DEFAULT_PLAYBACK_WIDTH = 1280
DEFAULT_PLAYBACK_HEIGHT = 720


def _put_status(pool, message, payload):
    try:
        pool.put((message, payload), timeout=0.2)
    except Exception:
        pass


def _load_entries(folder):
    root = Path(folder).expanduser()
    metadata_path = root / RECORDING_METADATA_NAME
    if not root.is_dir() or not metadata_path.is_file():
        raise ValueError("Select a recording folder containing recording.json")

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, list):
        raise ValueError(f"Invalid {RECORDING_METADATA_NAME} format")

    entries = []
    for item in metadata:
        camera = item.get("camera_frame")
        if not camera:
            continue
        point_cloud = root / item["point_cloud"]
        image = root / camera
        if not point_cloud.is_file() or not image.is_file():
            continue
        camera_time = item.get("camera_recorded_at")
        entries.append({
            "point_cloud": point_cloud,
            "image": image,
            "recorded_at": datetime.fromisoformat(item["recorded_at"]),
            "camera_recorded_at": (
                datetime.fromisoformat(camera_time) if camera_time else None
            ),
        })

    entries.sort(key=lambda item: item["recorded_at"])
    if not entries:
        raise ValueError("No recording frames have corresponding camera images")
    return root, entries


class SnapshotPlaybackController:
    def __init__(self, connection, pool, shutdown_event, initial_values):
        self.connection = connection
        self.pool = pool
        self.shutdown_event = shutdown_event
        self.filters = Filter_graph(initial_values)
        self.graph = Graph_radar(initial_values.get("point_cutoff", 15.0))
        self.active = False
        self.paused = False
        self.stop_requested = False
        self.width = DEFAULT_PLAYBACK_WIDTH
        self.height = DEFAULT_PLAYBACK_HEIGHT
        self.entries = []
        self.index = 0
        self.current_reader = None
        self.snapshot_folder = None

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
            elif event == "snapshot_playback_start":
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
        if self.active and self.entries:
            self._render()

    def _play(self, value):
        try:
            root, self.entries = _load_entries(value["folder"])
            self.snapshot_folder = value.get("snapshot_folder")
            self._set_resolution(value)
            self.active = True
            self.paused = True
            self.stop_requested = False
            self.index = 0
            _put_status(self.pool, "snapshot_playback_state", {
                "active": True,
                "paused": True,
                "current": 1,
                "total": len(self.entries),
                "folder": str(root.resolve()),
            })
            self._render()

            while (
                self.active
                and not self.stop_requested
                and not self.shutdown_event.is_set()
            ):
                delay = self._frame_delay()
                deadline = time.monotonic() + delay
                while (
                    self.active
                    and not self.stop_requested
                    and not self.shutdown_event.is_set()
                ):
                    if self.connection.poll(0.05):
                        event, payload = self.connection.recv()
                        self._handle(event, payload)
                        if not self.paused:
                            deadline = time.monotonic() + self._frame_delay()
                    elif not self.paused and time.monotonic() >= deadline:
                        if self.index + 1 >= len(self.entries):
                            self.paused = True
                            self._state()
                        else:
                            self.index += 1
                            self._render()
                        break

            _put_status(self.pool, "snapshot_playback_state", {
                "active": False,
                "paused": False,
                "current": 0,
                "total": len(self.entries),
                "completed": not self.stop_requested,
            })
        except Exception as error:
            _put_status(self.pool, "snapshot_playback_error", str(error))
            _put_status(self.pool, "snapshot_playback_state", {
                "active": False,
                "paused": False,
                "current": 0,
                "total": 0,
            })
        finally:
            self.active = False
            self.entries = []
            self.current_reader = None
            self._close_windows()

    def _frame_delay(self):
        if self.index + 1 >= len(self.entries):
            return 0.05
        return max(
            0.05,
            (
                self.entries[self.index + 1]["recorded_at"]
                - self.entries[self.index]["recorded_at"]
            ).total_seconds(),
        )

    def _handle(self, event, value):
        if event == "STOP":
            self.shutdown_event.set()
        elif event == "snapshot_playback_stop":
            self.stop_requested = True
        elif event == "snapshot_playback_pause":
            self.paused = not self.paused
            self._state()
        elif event == "snapshot_playback_next":
            self.paused = True
            self.index = min(self.index + 1, len(self.entries) - 1)
            self._render()
            self._state()
        elif event == "snapshot_playback_previous":
            self.paused = True
            self.index = max(self.index - 1, 0)
            self._render()
            self._state()
        elif event == "snapshot_playback_snapshot":
            try:
                destination = value.get("folder") if isinstance(value, dict) else None
                self._save_snapshot(destination)
            except Exception as error:
                _put_status(self.pool, "snapshot_playback_snapshot_error", str(error))
        elif event == "playback_resolution":
            try:
                self._set_resolution(value)
            except Exception as error:
                _put_status(self.pool, "playback_resolution_error", str(error))
        elif event == "point_cutoff":
            try:
                self.graph.set_distance_cutoff(value.get("distance", 15.0))
                if self.active and self.entries:
                    self._render()
            except Exception as error:
                _put_status(self.pool, "point_cutoff_error", str(error))
        elif isinstance(event, str) and event.startswith("filter"):
            self.filters.update_values(event, value)
            self._render()

    def _render(self):
        entry = self.entries[self.index]
        reader = PointCloudReader(entry["point_cloud"])
        self.current_reader = reader
        if reader.frame_type == "cluster":
            x, y, colors = self.filters.filter_point_sequence(reader.clusters)
        else:
            x, y, colors = self.filters.filter_object_sequence(reader.objects)
        self.graph.show_points(x, y, colors, self.filters.last_points)

        image = cv.imread(str(entry["image"]))
        if image is None:
            raise RuntimeError(f"Could not read image: {entry['image'].name}")
        image = cv.resize(
            image,
            (self.width, self.height),
            interpolation=cv.INTER_AREA,
        )
        cv.imshow("CAMERA PLAYBACK", image)
        cv.waitKey(1)
        _put_status(self.pool, "snapshot_playback_progress", {
            "current": self.index + 1,
            "total": len(self.entries),
            "file": entry["point_cloud"].name,
            "image": entry["image"].name,
        })

    def _state(self):
        _put_status(self.pool, "snapshot_playback_state", {
            "active": self.active,
            "paused": self.paused,
            "current": self.index + 1 if self.entries else 0,
            "total": len(self.entries),
        })

    def _save_snapshot(self, folder):
        if self.current_reader is None or not self.entries:
            raise RuntimeError("No playback frame is currently displayed")
        destination = folder or self.snapshot_folder
        if not destination:
            raise ValueError("Select a snapshot destination folder")

        entry = self.entries[self.index]
        camera_time = entry["camera_recorded_at"] or (
            entry["recorded_at"] + timedelta(seconds=CAMERA_DELAY_SECONDS)
        )
        result = ManualSnapshotWriter(destination).save(
            self.current_reader.points,
            entry["recorded_at"],
            self.current_reader.frame_type,
            entry["image"].read_bytes(),
            camera_time,
        )
        _put_status(self.pool, "snapshot_playback_snapshot_saved", result)

    @staticmethod
    def _close_windows():
        for name in ("RADAR", "CAMERA PLAYBACK"):
            try:
                cv.destroyWindow(name)
                cv.waitKey(1)
            except cv.error:
                pass


def snapshot_playback_main(connection, pool, shutdown_event, initial_values):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())
    SnapshotPlaybackController(connection, pool, shutdown_event, initial_values).run()
