from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import cv2 as cv

import CAPTURE.snapshot_playback as playback_module
from GRAPH.graph_draw import Graph_radar


class FakeConnection:
    def __init__(self, events):
        self.events = list(events)

    def poll(self, _timeout=None):
        return bool(self.events)

    def recv(self):
        return self.events.pop(0)


class FakePool:
    def __init__(self):
        self.items = []

    def put(self, item, timeout=None):
        self.items.append(item)


class FakeShutdownEvent:
    def __init__(self):
        self.stopped = False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True


class SnapshotPlaybackTests(unittest.TestCase):
    def test_playback_starts_playing_and_processes_clicks_while_paused(self):
        connection = FakeConnection([
            ("snapshot_playback_pause", None),
            ("snapshot_playback_stop", None),
        ])
        pool = FakePool()
        controller = playback_module.SnapshotPlaybackController(
            connection,
            pool,
            FakeShutdownEvent(),
            {},
        )
        entry = SimpleNamespace(recorded_at=datetime.now(timezone.utc))

        with (
            patch.object(playback_module, "_load_entries", return_value=(Path("."), [entry])),
            patch.object(controller, "_render"),
            patch.object(controller, "_close_windows"),
            patch.object(controller, "_process_window_events") as process_events,
        ):
            controller._play({"folder": "."})

        first_message, first_state = pool.items[0]
        self.assertEqual(first_message, "snapshot_playback_state")
        self.assertTrue(first_state["active"])
        self.assertFalse(first_state["paused"])
        self.assertGreaterEqual(process_events.call_count, 2)

    def test_clicked_point_is_printed_on_one_line(self):
        graph = Graph_radar.__new__(Graph_radar)
        graph.displayed_points = [{
            "pixel": (10, 20),
            "x": 1.25,
            "y": 3.5,
            "point": SimpleNamespace(dynamic_property=0, rcs=-7.0),
        }]
        output = StringIO()

        with redirect_stdout(output):
            graph._on_mouse(cv.EVENT_LBUTTONDOWN, 10, 20, None, None)

        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("[RADAR POINT] x=1.25 m | y=3.50 m", lines[0])


if __name__ == "__main__":
    unittest.main()
