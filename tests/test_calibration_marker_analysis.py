"""Generated image data only: no display/window/screenshot is opened."""

import unittest
import json
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import cv2 as cv
import numpy as np
import pygame

from CALIBRATION.calibration_screen_clock import CalibrationRenderer
from CALIBRATION.display_timing import DISPLAY_FORMAT
from CALIBRATION.marker_analysis import DisplayEvidence, MarkerAnalyzer, decode_bits, rectangle_points
from CALIBRATION.ean13 import ean13_bits
from analyze_calibration_recording_offset import analyze_recording, write_csv, main as analyze_main


def fixture(count=12, visible_frames=4):
    screen = pygame.Surface((1920, 1080))
    renderer = CalibrationRenderer(screen, visible_frames=visible_frames)
    frames = []
    period = round(1_000_000_000 / 60)
    # Include the following presentation in the journal, while photographing
    # the preceding state. Its timing is required to verify marker replacement.
    for index in range(count + 1):
        marker = 123_000_000_000 + index * period
        corner = renderer.render_next(marker) if index < count else index % 4
        frames.append({"index": index, "corner": corner, "marker_ns": marker,
                       "deadline_ns": marker + 1_000_000, "submit_ns": marker + 500_000,
                       "flip_return_ns": marker + 1_000_000, "frame_period_ns": period,
                       "interval_ns": period if index else None, "late_submit": False,
                       "skipped_periods": 0, "irregular_interval": False})
    metadata = {"format": DISPLAY_FORMAT, **renderer.metadata()}
    evidence = DisplayEvidence(metadata, frames)
    frame = np.transpose(pygame.surfarray.array3d(screen), (1, 0, 2))[:, :, ::-1].copy()
    return frame, evidence, renderer


class MarkerAnalysisTests(unittest.TestCase):
    def test_strict_guards_parity_and_checksum(self):
        bits = ean13_bits("000123456789")
        self.assertEqual(decode_bits(bits)[:12], "000123456789")
        self.assertIsNone(decode_bits("000" + bits[3:]))

    def test_direct_with_known_screen_plane(self):
        frame, evidence, _ = fixture()
        analyzer = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080)))
        result = analyzer.analyze(frame, 123200)
        self.assertEqual(result["selection"], "direct", result)
        self.assertEqual(result["display_index"], 11)
        self.assertEqual(result["source_corner"], 3)
        self.assertEqual(len(result["observations"]), 4)

    def test_all_visible_counts_and_blank_next_quadrant(self):
        for count in range(1, 5):
            with self.subTest(count=count):
                frame, evidence, _ = fixture(visible_frames=count)
                result = MarkerAnalyzer(evidence).analyze(frame, 123200)
                self.assertEqual(result["selection"], "direct", result)
                self.assertEqual(len(result["observations"]), count)
                self.assertEqual(result["next_corner"], 0)
                self.assertEqual(len(result["expected_empty_corners"]), 4 - count)
                if count == 3:
                    self.assertEqual(result["expected_empty_corners"], [0])

    def test_incomplete_barcode_artifact_in_blank_quadrant_is_rejected(self):
        frame, evidence, _ = fixture(visible_frames=3)
        x, y, width, height = evidence.metadata["layouts"][0]["bars"]
        frame[y:y + height // 3, x:x + width // 2] = 200
        result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
        self.assertEqual(result["reason"], "expected_blank_quadrant_has_content", result)
        self.assertEqual(result["unexpected_nonempty_corners"], [0])

    def test_three_markers_keep_predecessor_fallback_but_one_has_no_history(self):
        for count in (1, 3):
            with self.subTest(count=count):
                frame, evidence, _ = fixture(visible_frames=count)
                x, y, width, height = evidence.metadata["layouts"][3]["bars"]
                frame[y:y + height, x:x + width] = 200
                result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
                self.assertEqual(result["selection"], "inferred_one_period" if count == 3 else "ambiguous", result)

    def test_old_four_marker_journal_remains_supported(self):
        _, evidence, _ = fixture()
        metadata = dict(evidence.metadata)
        del metadata["visible_frames"]
        self.assertEqual(DisplayEvidence(metadata, evidence.frames).visible_frames, 4)

    def test_journal_pause_excludes_held_marker_even_before_receipt_of_pause(self):
        frame, evidence, _ = fixture()
        with TemporaryDirectory() as folder:
            rows = [{"kind": "session", **evidence.metadata}] + [
                {"kind": "frame", **row} for row in evidence.frames]
            rows.append({"kind": "pause", "paused": True, "last_frame_index": 11,
                         "monotonic_ns": 124_000_000_000})
            path = Path(folder) / "display_timestamps.jsonl"
            path.write_text("".join(json.dumps(row) + "\n" for row in rows))
            paused = DisplayEvidence.load(folder)
            self.assertEqual(paused.paused_indices, {11})
            result = MarkerAnalyzer(paused, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
            self.assertEqual(result["reason"], "display_marker_held_for_pause")
            self.assertIsNone(result["screen_ns"])

    def test_pause_before_first_frame_is_valid_but_unknown_index_is_rejected(self):
        _, evidence, _ = fixture()
        self.assertEqual(DisplayEvidence(evidence.metadata, [], [-1]).paused_indices, {-1})
        with self.assertRaises(ValueError):
            DisplayEvidence(evidence.metadata, evidence.frames, [len(evidence.frames)])

    def test_resume_boundary_and_paused_predecessor_are_not_used_for_timing(self):
        frame, evidence, _ = fixture()
        evidence.frames[11]["resumed_after_pause"] = True
        analyzer = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080)))
        result = analyzer.analyze(frame, 123200)
        self.assertEqual(result["reason"], "newest_display_timing_unstable")
        evidence.frames[11]["resumed_after_pause"] = False
        evidence.paused_indices = frozenset([10])
        x, y, width, height = evidence.metadata["layouts"][3]["bars"]
        frame[y:y + height, x:x + width] = 200
        result = analyzer.analyze(frame, 123200)
        self.assertEqual(result["reason"], "stable_measured_period_unavailable")

    def test_automatic_registration_on_perspective_image(self):
        frame, evidence, _ = fixture()
        corners = np.float32([[100, 50], [1170, 90], [1120, 650], [150, 600]])
        transform = cv.getPerspectiveTransform(rectangle_points((0, 0, 1920, 1080)), corners)
        camera = cv.warpPerspective(frame, transform, (1280, 720))
        result = MarkerAnalyzer(evidence).analyze(camera, 123200)
        self.assertEqual(result["selection"], "direct", result)
        self.assertEqual(result["display_index"], 11)

    def test_unreadable_newest_uses_one_measured_period(self):
        frame, evidence, _ = fixture()
        x, y, w, h = evidence.metadata["layouts"][3]["bars"]
        frame[y:y + h, x:x + w] = 200
        result = MarkerAnalyzer(evidence).analyze(frame, 123200)
        self.assertEqual(result["selection"], "inferred_one_period", result)
        self.assertEqual(result["source_corner"], 2)
        self.assertEqual(result["screen_ns"], (evidence.frames[10]["marker_ns"] // 1_000_000) * 1_000_000 + evidence.period_ns)

    def test_no_two_period_guess_and_no_inference_across_missed_refresh(self):
        frame, evidence, _ = fixture()
        for corner in (2, 3):
            x, y, w, h = evidence.metadata["layouts"][corner]["bars"]
            frame[y:y + h, x:x + w] = 200
        analyzer = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080)))
        result = analyzer.analyze(frame, 123200)
        self.assertEqual(result["reason"], "immediate_predecessor_not_readable")
        frame, evidence, _ = fixture()
        evidence.frames[11]["skipped_periods"] = 1
        result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
        self.assertEqual(result["reason"], "newest_display_timing_unstable")

    def test_two_outlines_are_rejected(self):
        frame, evidence, _ = fixture()
        x, y, w, h = evidence.metadata["layouts"][2]["outline"]
        cv.rectangle(frame, (x, y), (x + w - 1, y + h - 1), (255, 255, 255), 4)
        result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
        self.assertEqual(result["reason"], "missing_or_multiple_newest_outlines")

    def test_following_update_problems_reject_direct_and_inferred_markers(self):
        cases = (
            ({"skipped_periods": 2}, "replacement_missed_period_candidates"),
            ({"late_submit": True}, "replacement_late_submission"),
            ({"irregular_interval": True}, "replacement_irregular_interval"),
        )
        for changes, issue in cases:
            for inferred in (False, True):
                with self.subTest(issue=issue, inferred=inferred):
                    frame, evidence, _ = fixture()
                    evidence.frames[12].update(changes)
                    if inferred:
                        x, y, w, h = evidence.metadata["layouts"][3]["bars"]
                        frame[y:y + h, x:x + w] = 200
                    result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
                    self.assertEqual(result["reason"], "display_replacement_timing_unstable", result)
                    self.assertIsNone(result["screen_ns"])
                    self.assertEqual(result["display_index"], 11)
                    self.assertIn(issue, result["display_timing"]["issue_codes"])

    def test_raw_replacement_gap_detects_long_and_short_holds_without_flags(self):
        for factor, issue in ((2, "replacement_irregular_interval_long"),
                              (0.5, "replacement_irregular_interval_short"), (1.1, None)):
            with self.subTest(factor=factor):
                frame, evidence, _ = fixture()
                following = evidence.frames[12]
                gap = round(evidence.period_ns * factor)
                following["flip_return_ns"] = evidence.frames[11]["flip_return_ns"] + gap
                result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
                self.assertEqual(result["display_timing"]["hold_interval_proxy_ns"], gap)
                if issue:
                    self.assertIn(issue, result["display_timing"]["issue_codes"])
                    self.assertIsNone(result["screen_ns"])
                else:
                    self.assertEqual(result["selection"], "direct")

    def test_final_marker_is_unverified_even_when_journal_closed_cleanly(self):
        frame, evidence, _ = fixture()
        for summary in (None, {"kind": "summary", "frames": 12}):
            with self.subTest(summary=summary):
                final = DisplayEvidence(evidence.metadata, evidence.frames[:-1], summary=summary)
                result = MarkerAnalyzer(final, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
                self.assertEqual(result["reason"], "display_replacement_timing_unverified")
                self.assertEqual(result["display_timing"]["status"], "unknown")
                self.assertIn("replacement_evidence_missing", result["display_timing"]["issue_codes"])

    def test_missing_interval_is_unknown_and_older_complete_rows_still_work(self):
        frame, evidence, _ = fixture()
        for row in evidence.frames:
            for key in ("flip_return_ns", "submit_ns", "deadline_ns", "frame_period_ns"):
                del row[key]
        analyzer = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080)))
        self.assertEqual(analyzer.analyze(frame, 123200)["selection"], "direct")
        evidence.frames[12]["interval_ns"] = None
        result = analyzer.analyze(frame, 123200)
        self.assertIn("replacement_interval_unavailable", result["display_timing"]["issue_codes"])
        self.assertEqual(result["display_timing"]["status"], "unknown")

    def test_pause_gap_is_catalogued_as_pause_without_counting_missed_periods(self):
        _, evidence, _ = fixture()
        evidence.frames[12].update(resumed_after_pause=True, interval_ns=None,
                                   flip_return_ns=evidence.frames[12]["flip_return_ns"] + 10_000_000_000)
        timing = evidence.marker_timing(11)
        self.assertIsNone(timing["hold_interval_proxy_ns"])
        self.assertIn("replacement_resumed_after_pause", timing["issue_codes"])
        catalog = evidence.timing_catalog()
        self.assertEqual(catalog["totals"]["missed_period_candidates"], 0)
        self.assertEqual(catalog["event_issue_counts"], {"resumed_after_pause": 1})

    def test_inference_timing_failures_have_specific_diagnostics(self):
        for count, issue in ((4, "inference_period_unavailable"),
                             (12, "predecessor_marker_interval_irregular")):
            with self.subTest(issue=issue):
                frame, evidence, _ = fixture(count=count)
                if count == 12:
                    evidence.frames[11]["marker_ns"] -= 8_000_000
                    evidence = DisplayEvidence(evidence.metadata, evidence.frames)
                x, y, w, h = evidence.metadata["layouts"][3]["bars"]
                frame[y:y + h, x:x + w] = 200
                result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(frame, 123200)
                self.assertIsNone(result["screen_ns"])
                self.assertIn(issue, result["display_timing"]["issue_codes"])
                self.assertNotEqual(result["display_timing"]["status"], "clean")

    def test_report_and_diagnostics_csv_catalogue_affected_images_without_biasing_offsets(self):
        clean, _, _ = fixture(count=11)
        held, evidence, _ = fixture()
        inferred_held = held.copy()
        x, y, w, h = evidence.metadata["layouts"][3]["bars"]
        inferred_held[y:y + h, x:x + w] = 200
        following = evidence.frames[12]
        following.update(skipped_periods=1, late_submit=True, irregular_interval=True,
                         submit_ns=following["deadline_ns"] + 1,
                         flip_return_ns=following["flip_return_ns"] + evidence.period_ns)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            display_rows = [{"kind": "session", **evidence.metadata}] + [
                {"kind": "frame", **row} for row in evidence.frames]
            (root / "display_timestamps.jsonl").write_text("".join(json.dumps(row) + "\n" for row in display_rows))
            rows = []
            for index, image in enumerate((clean, held, inferred_held)):
                filename = f"camera_{index:06d}.jpg"
                self.assertTrue(cv.imwrite(str(root / filename), image))
                rows.append({"camera_frame": filename, "timing": {
                    "host_monotonic_received_ns": 123_300_000_000 + index * 33_333_333,
                    "pipeline_age_ns": 100_000_000}})
            (root / "camera_timestamps.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            arguments = ["analyze", str(root), "--output-dir", str(root), "--screen-corners",
                         "0", "0", "1919", "0", "1919", "1079", "0", "1079"]
            with patch("sys.argv", arguments):
                analyze_main()
            report = json.loads((root / "calibration_offset_analysis.json").read_text())
            self.assertEqual(report["metrics"]["pts_screen_offset_ms"]["count"], 1)
            self.assertEqual(report["frames"][0]["camera_frame"], "camera_000000.jpg")
            self.assertEqual(report["display_timing"]["camera_frames_excluded"], 2)
            self.assertEqual(report["exclusion_reason_counts"], {"display_replacement_timing_unstable": 2})
            self.assertEqual(report["display_timing"]["totals"]["timing_events"], 1)
            self.assertEqual(report["display_timing"]["events"][0]["affected_display_indices"], [11, 12])
            self.assertEqual(report["display_timing"]["camera_issue_counts"]["replacement_late_submission"], 2)
            with (root / "calibration_frame_diagnostics.csv").open() as handle:
                diagnostics = list(csv.DictReader(handle))
            self.assertEqual([row["accepted"] for row in diagnostics], ["True", "False", "False"])
            self.assertIn("replacement_irregular_interval_long", json.loads(diagnostics[1]["display_timing_issue_codes"]))
            self.assertEqual(json.loads(diagnostics[1]["display_timing"])["replacement"]["index"], 12)
            # A truncated recording may leave every selected marker unverified.
            # Still emit usable diagnostics and empty offset statistics.
            self.assertTrue(cv.imwrite(str(root / "camera_000000.jpg"), held))
            (root / "display_timestamps.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in display_rows[:-1]) + '{"kind":')
            with patch("sys.argv", arguments):
                analyze_main()
            report = json.loads((root / "calibration_offset_analysis.json").read_text())
            self.assertEqual(report["counts"]["accepted_frames"], 0)
            self.assertEqual(report["counts"]["excluded_frames"], 3)
            self.assertIsNone(report["metrics"]["pts_screen_offset_ms"])
            self.assertEqual(report["display_timing"]["camera_frames_excluded"], 3)
            self.assertTrue(all(row["display_timing_status"] == "unknown" for row in report["frame_diagnostics"]))
            self.assertFalse(report["display_timing"]["journal_summary_present"])
            self.assertTrue((root / "calibration_offset_frames.csv").read_text().startswith("camera_frame,"))

    def test_valid_scanline_disagreement_is_not_voted_away(self):
        before, evidence, renderer = fixture()
        renderer.render_next(123_200_000_000)
        after = np.transpose(pygame.surfarray.array3d(renderer.target), (1, 0, 2))[:, :, ::-1].copy()
        x, y, w, h = evidence.metadata["layouts"][0]["bars"]
        before[y:y + h // 2, x:x + w] = after[y:y + h // 2, x:x + w]
        result = MarkerAnalyzer(evidence, rectangle_points((0, 0, 1920, 1080))).analyze(before, 123200)
        self.assertEqual(result["selection"], "ambiguous")
        self.assertTrue(any(item["transition"] for item in result["observations"]))

    def test_report_separates_direct_inferred_and_excluded_frames(self):
        frame, evidence, _ = fixture()
        inferred = frame.copy()
        x, y, w, h = evidence.metadata["layouts"][3]["bars"]
        inferred[y:y + h, x:x + w] = 200
        ambiguous = frame.copy()
        x, y, w, h = evidence.metadata["layouts"][2]["outline"]
        cv.rectangle(ambiguous, (x, y), (x + w - 1, y + h - 1), (255, 255, 255), 4)
        with TemporaryDirectory() as folder:
            root = Path(folder)
            display_rows = [{"kind": "session", **evidence.metadata}] + [
                {"kind": "frame", **row} for row in evidence.frames]
            (root / "display_timestamps.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in display_rows))
            rows = []
            for index, image in enumerate((frame, inferred, ambiguous)):
                filename = f"camera_{index:06d}.jpg"
                self.assertTrue(cv.imwrite(str(root / filename), image))
                rows.append({"camera_frame": filename, "timing": {
                    "host_monotonic_received_ns": 123_300_000_000 + index * 33_333_333,
                    "pipeline_age_ns": 100_000_000}})
            (root / "camera_timestamps.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows))
            result = analyze_recording(root, screen_corners=rectangle_points((0, 0, 1920, 1080)))
            self.assertEqual(result["counts"]["direct_frames"], 1)
            self.assertEqual(result["counts"]["inferred_one_period_frames"], 1)
            self.assertEqual(len(result["excluded_frames"]), 1)
            self.assertIn("direct", result["metrics_by_selection"])
            self.assertIn("inferred_one_period", result["metrics_by_selection"])
            self.assertGreater(result["frames"][1]["added_period_ns"], 0)
            write_csv(root / "results.csv", result["frames"])
            self.assertIn("barcode_observations", (root / "results.csv").read_text())
            write_csv(root / "empty.csv", [])
            self.assertTrue((root / "empty.csv").read_text().startswith("camera_frame,"))

    def test_missing_required_journal_is_not_silently_treated_as_legacy(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(DisplayEvidence.load(root))
            (root / "camera_timing_session.json").write_text(
                json.dumps({"display_journal": "display_timestamps.jsonl"}))
            with self.assertRaises(FileNotFoundError):
                DisplayEvidence.load(root)

    def test_partial_final_journal_line_is_ignored_but_internal_gap_is_rejected(self):
        _, evidence, _ = fixture()
        with TemporaryDirectory() as folder:
            path = Path(folder) / "display_timestamps.jsonl"
            rows = [{"kind": "session", **evidence.metadata}] + [
                {"kind": "frame", **row} for row in evidence.frames]
            text = "".join(json.dumps(row) + "\n" for row in rows)
            path.write_text(text + '{"kind":')
            self.assertEqual(len(DisplayEvidence.load(folder).frames), 13)
            path.write_text(text + '{"kind":\n')
            with self.assertRaises(ValueError):
                DisplayEvidence.load(folder)
            with self.assertRaises(ValueError):
                DisplayEvidence(evidence.metadata, evidence.frames[1:])


if __name__ == "__main__":
    unittest.main()
