import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pygame

from CALIBRATION.calibration_screen_clock import (
    CalibrationRenderer,
    format_monotonic_timestamp,
    run_calibration_clock,
)
from CALIBRATION.display_timing import DisplayJournal, FramePacer
from CALIBRATION.ean13 import (
    EAN13Painter,
    ean13_bits,
    ean13_check_digit,
    monotonic_ms_payload,
)


class CalibrationClockTests(unittest.TestCase):
    def test_monotonic_time_becomes_a_twelve_digit_payload(self):
        self.assertEqual(monotonic_ms_payload(123_456_789_000_000), "000123456789")

    def test_ean13_encoding_has_valid_size_and_guards(self):
        payload = "123456789012"
        self.assertEqual(ean13_check_digit(payload), "8")
        bits = ean13_bits(payload)
        self.assertEqual(len(bits), 95)
        self.assertTrue(bits.startswith("101"))
        self.assertTrue(bits.endswith("101"))

    def test_display_label_uses_monotonic_time_without_prediction(self):
        timestamp_ns = 12_345_678_000_000
        self.assertEqual(format_monotonic_timestamp(timestamp_ns), "12 345.678")

    def test_four_persistent_corners_and_one_white_outline(self):
        screen = pygame.Surface((960, 540))
        renderer = CalibrationRenderer(screen, visible_frames=4)
        for index in range(4):
            self.assertEqual(renderer.render_next((index + 1) * 1_000_000), index)
        self.assertEqual(renderer.timestamps, [1_000_000, 2_000_000, 3_000_000, 4_000_000])
        layouts = renderer.metadata()["layouts"]
        unchanged = screen.subsurface(pygame.Rect(layouts[1]["area"])).copy()
        renderer.render_next(5_000_000)
        self.assertEqual(renderer.timestamps, [5_000_000, 2_000_000, 3_000_000, 4_000_000])
        self.assertEqual(pygame.image.tobytes(unchanged, "RGB"),
                         pygame.image.tobytes(screen.subsurface(layouts[1]["area"]), "RGB"))
        for index, layout in enumerate(layouts):
            x, y, _, _ = layout["outline"]
            self.assertEqual(screen.get_at((x + 1, y + 1))[:3],
                             (255, 255, 255) if index == 0 else (50, 50, 50))

    def test_optimized_painter_matches_reference_bits_and_quiet_zones(self):
        screen = pygame.Surface((1000, 100))
        painter = EAN13Painter((10, 10, 980, 80))
        for payload in ("000123456789", "123456789012", "999999999999"):
            painter.draw(screen, payload)
            x, y, width, height = painter.bars
            modules = width // 95
            actual = "".join("1" if screen.get_at((x + i * modules, y))[0] == 50 else "0"
                             for i in range(95))
            self.assertEqual(actual, ean13_bits(payload))
            self.assertEqual(screen.get_at((x - 1, y))[:3], (200, 200, 200))
            self.assertEqual(screen.get_at((x + width, y + height - 1))[:3], (200, 200, 200))

    def test_visible_history_limits_and_clears_entire_expired_quadrants(self):
        for count in range(1, 5):
            with self.subTest(visible_frames=count):
                screen = pygame.Surface((960, 540))
                renderer = CalibrationRenderer(screen, visible_frames=count)
                for index in range(12):
                    corner = renderer.render_next((index + 1) * 1_000_000)
                    occupied = {(corner - age) % 4 for age in range(min(count, index + 1))}
                    self.assertEqual({slot for slot, value in enumerate(renderer.timestamps) if value is not None}, occupied)
                    for slot, layout in enumerate(renderer.metadata()["layouts"]):
                        if slot not in occupied:
                            pixels = pygame.surfarray.array3d(screen.subsurface(layout["area"]))
                            self.assertTrue((pixels == 50).all())
                    if count == 3 and index >= 2:
                        self.assertIsNone(renderer.timestamps[(corner + 1) % 4])
                self.assertEqual(renderer.metadata()["visible_frames"], count)

    def test_default_is_three_and_invalid_history_is_rejected(self):
        screen = pygame.Surface((960, 540))
        self.assertEqual(CalibrationRenderer(screen).visible_frames, 3)
        for count in (0, 5, 2.5, True, "3"):
            with self.subTest(count=count), self.assertRaises(ValueError):
                CalibrationRenderer(screen, visible_frames=count)

    def test_painter_rejects_invalid_payload_and_narrow_canvas(self):
        with self.assertRaises(ValueError):
            ean13_check_digit("１２３４５６７８９０１２")
        with self.assertRaises(ValueError):
            EAN13Painter((0, 0, 112, 20))
        with self.assertRaises(ValueError):
            CalibrationRenderer(pygame.Surface((240, 160)))

    def test_deadlines_skip_missed_periods_without_drift_or_catchup(self):
        pacer = FramePacer(0)
        period = pacer.period_ns
        self.assertEqual(pacer.skip_expired(period * 3 + 1234), 3)
        self.assertEqual(pacer.deadline_ns, period * 4)
        timing = pacer.observe(period * 4 - 1_000_000, period * 4 - 500_000, period * 4, 3)
        self.assertEqual(timing["skipped_periods"], 3)
        self.assertEqual(pacer.deadline_ns, period * 5)

    def test_slow_flips_are_reported_not_learned_as_a_lower_refresh_rate(self):
        pacer = FramePacer(0)
        period = pacer.period_ns
        for index in range(12):
            target = pacer.deadline_ns
            result = pacer.observe(target - 1_000_000, target + 10, target + period)
            self.assertTrue(result["late_submit"])
            self.assertGreaterEqual(result["skipped_periods"], 1)
        self.assertEqual(pacer.period_ns, period)

    def test_journal_keeps_raw_times_and_refuses_overwrite(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "display_timestamps.jsonl"
            journal = DisplayJournal(path, {"size": [960, 540]})
            pacer = FramePacer(0)
            period = pacer.period_ns
            timing = pacer.observe(period - 1_000_000, period - 500_000, period)
            journal.append(0, timing)
            journal.pause(True, period + 1)
            journal.pause(False, period * 100)
            journal.close()
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[1]["marker_ns"], period - 1_000_000)
            self.assertEqual(rows[1]["flip_return_ns"], period)
            self.assertEqual(rows[-1]["frames"], 1)
            self.assertEqual(rows[2], {"kind": "pause", "paused": True,
                                      "monotonic_ns": period + 1, "last_frame_index": 0})
            self.assertFalse(rows[3]["paused"])
            self.assertEqual(journal.skipped, 0)
            with self.assertRaises(FileExistsError):
                DisplayJournal(path, {})

    def test_run_presents_once_per_marker_on_the_same_canvas_without_a_worker(self):
        screen = pygame.Surface((960, 540))
        pacer = Mock()
        pacer.wait.side_effect = lambda interrupted: (not interrupted(), 0)
        pacer.observe.return_value = {"marker_ns": 123, "skipped_periods": 0,
                                      "irregular_interval": False, "late_submit": False}
        journal = Mock()
        with patch("CALIBRATION.calibration_screen_clock.FramePacer", return_value=pacer), \
                patch("CALIBRATION.calibration_screen_clock.DisplayJournal", return_value=journal), \
                patch.object(pygame.display, "init"), \
                patch.object(pygame.display, "set_mode", return_value=screen) as mode, \
                patch.object(pygame.display, "set_caption"), \
                patch.object(pygame.display, "get_driver", return_value="fake"), \
                patch.object(pygame.display, "flip") as flip, \
                patch.object(pygame.display, "update") as update, \
                patch.object(pygame.event, "get", side_effect=[[], [pygame.event.Event(pygame.QUIT)]]), \
                patch.object(pygame, "quit"):
            run_calibration_clock(width=960, height=540, windowed=True)
        self.assertEqual(mode.call_count, 1)
        self.assertEqual(flip.call_count, 2)  # initial blank + one marker
        update.assert_not_called()
        journal.append.assert_called_once()
        self.assertEqual(journal.append.call_args.args[0], 0)
        journal.close.assert_called_once()

    def _run_control_events(self, batches, stop_event=None):
        """Drive controls with fake timing and off-screen pixels, never a window."""
        screen = pygame.Surface((960, 540))
        snapshots = []
        batches = iter(batches)
        journal = Mock()
        clock = Mock()
        clock.get_time.return_value = 17

        def events():
            snapshots.append(pygame.image.tobytes(screen, "RGB"))
            return next(batches)

        def new_pacer(*args):
            pacer = Mock()
            pacer.wait.side_effect = lambda interrupted: (not interrupted(), 0)
            pacer.observe.side_effect = lambda *args: {
                "skipped_periods": 0, "irregular_interval": False, "late_submit": False,
            }
            return pacer

        with patch("CALIBRATION.calibration_screen_clock.FramePacer", side_effect=new_pacer) as factory, \
                patch("CALIBRATION.calibration_screen_clock.DisplayJournal", return_value=journal), \
                patch.object(pygame.display, "init"), \
                patch.object(pygame.display, "set_mode", return_value=screen), \
                patch.object(pygame.display, "set_caption"), \
                patch.object(pygame.display, "get_driver", return_value="fake"), \
                patch.object(pygame.display, "flip") as flip, \
                patch.object(pygame.event, "get", side_effect=events), \
                patch.object(pygame.time, "Clock", return_value=clock), \
                patch.object(pygame, "quit"):
            run_calibration_clock(stop_event, width=960, height=540, windowed=True)
        return journal, factory, flip, clock, snapshots

    def test_p_toggles_freeze_and_resets_cadence_without_extra_presentations(self):
        press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)
        repeat = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p, repeat=True)
        journal, factory, flip, clock, snapshots = self._run_control_events([
            [], [press], [repeat], [press], [], [pygame.event.Event(pygame.QUIT)],
        ])
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(flip.call_count, 3)  # blank, before pause, after resume
        self.assertEqual([call.args[0] for call in journal.pause.call_args_list], [True, False])
        self.assertEqual([call.args[0] for call in journal.append.call_args_list], [0, 1])
        self.assertEqual([call.args[1]["resumed_after_pause"]
                          for call in journal.append.call_args_list], [False, True])
        self.assertTrue(all(snapshot == snapshots[1] for snapshot in snapshots[2:5]))
        self.assertNotEqual(snapshots[0], snapshots[1])
        self.assertNotEqual(snapshots[4], snapshots[5])
        clock.tick.assert_any_call(30)
        journal.close.assert_called_once()

    def test_pause_before_first_marker_and_all_exit_controls_remain_responsive(self):
        press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)
        exits = [pygame.event.Event(pygame.QUIT),
                 pygame.event.Event(pygame.KEYDOWN, key=pygame.K_q),
                 pygame.event.Event(pygame.KEYDOWN, key=pygame.K_ESCAPE)]
        for event in exits:
            with self.subTest(event=event):
                journal, factory, flip, _, _ = self._run_control_events([[press], [event]])
                self.assertEqual(flip.call_count, 1)
                self.assertEqual(factory.call_count, 1)
                journal.append.assert_not_called()
                journal.close.assert_called_once()
        stop = Mock()
        stop.is_set.side_effect = [False, True]
        journal, _, flip, _, _ = self._run_control_events([[press]], stop_event=stop)
        self.assertEqual(flip.call_count, 1)
        journal.close.assert_called_once()

    def test_pause_and_resume_in_one_batch_abort_the_old_wait(self):
        press = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_p)
        journal, factory, flip, _, _ = self._run_control_events([
            [press, press], [], [pygame.event.Event(pygame.QUIT)],
        ])
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(flip.call_count, 2)
        self.assertTrue(journal.append.call_args.args[1]["resumed_after_pause"])

    def test_pacer_learns_small_refresh_difference_from_clean_blocking_flips(self):
        pacer = FramePacer(0)
        measured = round(1_000_000_000 / 59.94)
        for index in range(12):
            returned = (index + 1) * measured
            pacer.observe(returned - 1_000_000, returned - 500_000, returned)
        self.assertEqual(pacer.period_ns, measured)

    def test_early_software_flips_do_not_accelerate_the_deadline_grid(self):
        pacer = FramePacer(0)
        period = pacer.period_ns
        for index in range(120):
            target = pacer.deadline_ns
            pacer.observe(target - 1_500_000, target - 900_000, target - 500_000)
            self.assertEqual(pacer.period_ns, period)
            self.assertEqual(pacer.deadline_ns, (index + 2) * period)

    def test_wait_can_exit_without_opening_a_display(self):
        pacer = FramePacer(0)
        with patch("CALIBRATION.display_timing.time.monotonic_ns", return_value=0):
            self.assertEqual(pacer.wait(lambda: True), (False, 0))


if __name__ == "__main__":
    unittest.main()
