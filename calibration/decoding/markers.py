"""OpenCV marker decoding with separate current-indicator and timing evidence.

Old outline journals remain readable. New recordings use an underline outside
barcode regions; neither kind of indicator is passed into regional decoding.
"""

import json
from collections import Counter
from pathlib import Path
import statistics

import cv2 as cv
import numpy as np

from .opencv import OpenCVReader, normalized_code
from calibration.display.timing import (
    DISPLAY_FORMAT, DISPLAY_JOURNAL_NAME, display_update_evidence, display_timing_event,
)


def rectangle_points(rect):
    x, y, width, height = rect
    return np.float32([[x, y], [x + width - 1, y],
                       [x + width - 1, y + height - 1], [x, y + height - 1]])


def ordered_quad(points):
    points = np.float32(points).reshape(4, 2)
    # Screen orientation is upright; arbitrary camera roll/mirroring requires
    # explicitly ordered --screen-corners instead of guessing orientation.
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).ravel()
    result = points[[np.argmin(sums), np.argmin(differences),
                     np.argmax(sums), np.argmax(differences)]]
    return result if len(np.unique(result, axis=0)) == 4 else None


class DisplayEvidence:
    def __init__(self, metadata, frames, paused_indices=(), summary=None):
        if metadata.get("format") != DISPLAY_FORMAT or len(metadata.get("layouts", [])) != 4:
            raise ValueError("Unsupported display timing journal")
        self.metadata = metadata
        # Journals from the first persistent-canvas version retained all four.
        self.visible_frames = metadata.get("visible_frames", 4)
        if type(self.visible_frames) is not int or not 1 <= self.visible_frames <= 4:
            raise ValueError("Invalid visible-frame count in display journal")
        self.frames = frames
        self.summary = summary
        self.paused_indices = frozenset(paused_indices)
        if any(type(index) is not int or not -1 <= index < len(frames)
               for index in self.paused_indices):
            raise ValueError("Invalid paused marker index in display journal")
        self.by_ms = {}
        size = metadata.get("size", [])
        if len(size) != 2 or any(not isinstance(value, int) or value <= 0 for value in size):
            raise ValueError("Invalid display canvas size")
        self.indicator_style = metadata.get("indicator_style", "outline")
        if self.indicator_style not in {"outline", "underline"}:
            raise ValueError("Invalid current indicator style")
        thickness = metadata.get("indicator_width", metadata.get("outline_width"))
        if type(thickness) is not int or thickness <= 0:
            raise ValueError("Invalid display indicator width")
        self.indicator_width = thickness
        for layout in metadata["layouts"]:
            for name in ("area", "barcode", "bars", self.indicator_style):
                rect = layout.get(name, [])
                if (len(rect) != 4 or any(not isinstance(value, int) for value in rect)
                        or min(rect) < 0 or rect[2] == 0 or rect[3] == 0
                        or rect[0] + rect[2] > size[0] or rect[1] + rect[3] > size[1]):
                    raise ValueError(f"Invalid display {name} rectangle")
        last_marker = -1
        for index, row in enumerate(frames):
            if row.get("index") != index or row.get("corner") != index % 4:
                raise ValueError("Display journal has missing/reordered frames")
            marker = int(row["marker_ns"])
            if marker <= last_marker:
                raise ValueError("Display marker times must be strictly increasing")
            last_marker = marker
            self.by_ms.setdefault(marker // 1_000_000, []).append(row)
        intervals = [row["interval_ns"] for row in frames
                     if row.get("interval_ns") and self.clean(row)]
        self.period_ns = round(statistics.median(intervals)) if len(intervals) >= 8 else None

    @staticmethod
    def clean(row):
        return not (row.get("late_submit") or row.get("skipped_periods")
                    or row.get("irregular_interval") or row.get("resumed_after_pause"))

    def update_evidence(self, index):
        return display_update_evidence(
            self.frames[index], self.frames[index - 1] if index else None, self.period_ns)

    def marker_timing(self, index):
        """Check how a marker arrived AND how it was replaced, without using camera receipt time."""
        current = self.update_evidence(index)
        following = self.update_evidence(index + 1) if index + 1 < len(self.frames) else None
        issues = ["marker_" + issue for issue in current["issues"]]
        if index in self.paused_indices:
            issues.append("marker_held_for_pause")
        if following is None:
            issues.append("replacement_evidence_missing")
        else:
            issues.extend("replacement_" + issue for issue in following["issues"])
            if following["interval_ns"] is None or not following["frame_period_ns"]:
                issues.append("replacement_interval_unavailable")
        unknown = {"replacement_evidence_missing", "replacement_interval_unavailable"}
        status = "clean"
        if issues:
            status = "suspect" if any(issue not in unknown for issue in issues) else "unknown"
        return {
            "status": status,
            "issue_codes": issues, "marker": current, "replacement": following,
            "hold_interval_proxy_ns": following["interval_ns"] if following else None,
            "excess_hold_proxy_ns": following["excess_interval_ns"] if following else None,
        }

    def timing_catalog(self):
        """Reconstruct events from raw rows, including journals predating timing_event rows."""
        events = []
        for index in range(len(self.frames)):
            event = display_timing_event(self.update_evidence(index), self.frames[index - 1] if index else None)
            if event is not None:
                events.append(event)
        return {
            "scope": "entire display session, including camera warm-up",
            "semantics": "software timing suspicion; no physical exposure or scanout correction",
            "journal_summary_present": self.summary is not None,
            "recorded_summary": self.summary,
            "totals": {
                "presented_markers": len(self.frames),
                "missed_period_candidates": sum(row.get("skipped_periods", 0) for row in self.frames),
                "irregular_intervals": sum(bool(row.get("irregular_interval")) for row in self.frames),
                "late_submissions": sum(bool(row.get("late_submit")) for row in self.frames),
                "timing_events": len(events),
            },
            "event_issue_counts": dict(Counter(issue for event in events for issue in event["update"]["issues"])),
            "affected_display_indices": sorted({index for event in events for index in event["affected_display_indices"]}),
            "pause_held_display_indices": sorted(index for index in self.paused_indices if index >= 0),
            "unverified_final_display_index": len(self.frames) - 1 if self.frames else None,
            "events": events,
        }

    def lookup(self, code, reference_ms):
        encoded = int(code[:12])
        modulus = 10**12
        base = reference_ms - reference_ms % modulus
        value = min((base - modulus + encoded, base + encoded, base + modulus + encoded),
                    key=lambda candidate: abs(candidate - reference_ms))
        if abs(value - reference_ms) > 60_000:
            return None
        rows = self.by_ms.get(value, [])
        return rows[0] if len(rows) == 1 else None

    @classmethod
    def load(cls, directory):
        path = Path(directory) / DISPLAY_JOURNAL_NAME
        if not path.is_file():
            session_path = Path(directory) / "camera_timing_session.json"
            if session_path.is_file():
                session = json.loads(session_path.read_text(encoding="utf-8"))
                if session.get("display_journal"):
                    raise FileNotFoundError("This recording requires its missing display_timestamps.jsonl journal")
            return None
        with path.open(encoding="utf-8") as handle:
            lines = handle.readlines()
        values = []
        for number, line in enumerate(lines, 1):
            try:
                values.append(json.loads(line))
            except json.JSONDecodeError as error:
                # A killed display can leave a partial final write, not a hole
                # in the middle of the journal. Never infer a missing frame.
                if number == len(lines) and not line.endswith("\n"):
                    break
                raise ValueError(f"Invalid display journal line {number}") from error
        if not values or values[0].get("kind") != "session":
            raise ValueError("Missing display journal session header")
        return cls(values[0], [row for row in values[1:] if row.get("kind") == "frame"],
                   [row.get("last_frame_index") for row in values[1:]
                    if row.get("kind") == "pause" and row.get("paused") is True],
                   next((row for row in reversed(values) if row.get("kind") == "summary"), None))


def scan_panel(gray, layout, reader):
    """Read separate bands with OpenCV; never vote away a valid transition."""
    quad = rectangle_points(layout["barcode"])[[3, 0, 1, 2]]
    symbols = reader.decode_regions(gray, [(0, quad)])
    codes = [code for symbol in symbols
             if (code := normalized_code(symbol["raw_code"], symbol["type"]))]
    return sorted(set(codes)), len(codes)


def indicator_present(gray, layout, style, thickness):
    if style == "outline":
        return outline_present(gray, layout, thickness)
    x, y, width, height = layout["underline"]
    top, bottom = max(0, y-4), min(gray.shape[0], y+height+4)
    line = gray[top:bottom, x+width//10:x+width-width//10]
    left = gray[top:bottom, max(0, x-12):max(0, x-6)]
    right = gray[top:bottom, x+width+6:x+width+12]
    if not line.size or not left.size or not right.size:
        return False
    # Local contrast resists dim scenes; text and barcode pixels lie elsewhere.
    background = max(float(np.median(left)), float(np.median(right)))
    return bool(np.mean(np.max(line, axis=0) > background+25) >= .8)


def outline_present(gray, layout, thickness):
    x, y, width, height = layout["outline"]
    inset = thickness / 2
    xs = np.linspace(x + width * 0.1, x + width * 0.9, 40).astype(int)
    ys = np.linspace(y + height * 0.1, y + height * 0.9, 40).astype(int)
    sides = (
        (xs, np.full(40, round(y + inset))),
        (xs, np.full(40, round(y + height - 1 - inset))),
        (np.full(40, round(x + inset)), ys),
        (np.full(40, round(x + width - 1 - inset)), ys),
    )
    bx, by, bw, bh = layout["bars"]
    panel = gray[by:by + bh: max(1, bh // 20), bx:bx + bw]
    if not panel.size:
        return False
    dark, light = np.percentile(panel, (10, 90))
    threshold = max(dark + 30, dark + (light - dark) * 0.8)
    for side, (sx, sy) in enumerate(sides):
        # Perspective resampling can shift a four-pixel outline by a pixel or
        # two. Search across its width, never across the gap into barcode white.
        samples = np.stack([
            gray[np.clip(sy + (offset if side < 2 else 0), 0, gray.shape[0] - 1),
                 np.clip(sx + (offset if side >= 2 else 0), 0, gray.shape[1] - 1)]
            for offset in range(-2, 3)
        ])
        if np.mean(samples.max(axis=0) > threshold) < 0.8:
            return False
    return True


def select_marker(observations, outlined, evidence):
    """Return a direct sample or exactly one-period estimate, with provenance."""
    result = {"selection": "ambiguous", "reason": None, "screen_ns": None,
              "observations": observations, "outlined_corners": outlined}
    def reject(reason):
        result["reason"] = reason
        return result
    if len(outlined) != 1:
        return reject("missing_or_multiple_newest_indicators")
    if any(item.get("transition") for item in observations):
        return reject("differing_valid_band_values")
    usable = {item["corner"]: item for item in observations if item.get("display_index") is not None}
    if not usable:
        return reject("no_registered_barcode")
    newest_corner = outlined[0]
    direct = usable.get(newest_corner)
    previous = usable.get((newest_corner - 1) % 4)
    if direct:
        index = direct["display_index"]
    elif previous:
        index = previous["display_index"] + 1
    else:
        return reject("immediate_predecessor_not_readable")
    if index >= len(evidence.frames):
        return reject("display_evidence_missing")
    newest = evidence.frames[index]
    result["display_index"] = index
    result["display_timing"] = evidence.marker_timing(index)
    # The same optical payload exists before and throughout the pause. Exclude
    # every occurrence rather than guessing exposure time from receipt time.
    if index in evidence.paused_indices:
        return reject("display_marker_held_for_pause")
    if newest["corner"] != newest_corner:
        return reject("indicator_sequence_mismatch")
    result["next_corner"] = (newest_corner + 1) % 4
    occupied = {(newest_corner - age) % 4 for age in range(min(evidence.visible_frames, index + 1))}
    result["expected_empty_corners"] = sorted(set(range(4)) - occupied)
    if any(not 0 <= index - item["display_index"] < evidence.visible_frames for item in usable.values()):
        return reject("mixed_display_generations")
    if result["display_timing"]["marker"]["issues"]:
        return reject("newest_display_timing_unstable")
    if result["display_timing"]["status"] != "clean":
        return reject("display_replacement_timing_unverified" if result["display_timing"]["status"] == "unknown"
                      else "display_replacement_timing_unstable")
    if direct:
        result.update(selection="direct", screen_ns=direct["timestamp_ms"] * 1_000_000,
                      source_ean13=direct["code"], source_corner=newest_corner,
                      display_index=index, added_period_ns=0)
        return result
    period = evidence.period_ns
    preceding = evidence.frames[index - 1]
    predecessor_timing = evidence.marker_timing(index - 1)
    result["display_timing"]["inference_predecessor"] = predecessor_timing
    if predecessor_timing["status"] != "clean":
        result["display_timing"]["status"] = predecessor_timing["status"]
        result["display_timing"]["issue_codes"].extend("predecessor_" + issue for issue in predecessor_timing["issue_codes"])
    if period is None:
        if result["display_timing"]["status"] == "clean":
            result["display_timing"]["status"] = "unknown"
        result["display_timing"]["issue_codes"].append("inference_period_unavailable")
    if period is None or predecessor_timing["status"] != "clean":
        return reject("stable_measured_period_unavailable")
    marker_interval = newest["marker_ns"] - preceding["marker_ns"]
    result["display_timing"]["predecessor_marker_interval_ns"] = marker_interval
    if not 0.75 * period <= marker_interval <= 1.25 * period:
        result["display_timing"]["status"] = "suspect"
        result["display_timing"]["issue_codes"].append("predecessor_marker_interval_irregular")
        return reject("predecessor_marker_interval_irregular")
    result.update(selection="inferred_one_period",
                  screen_ns=previous["timestamp_ms"] * 1_000_000 + period,
                  source_ean13=previous["code"], source_corner=previous["corner"],
                  display_index=index, added_period_ns=period)
    return result


class MarkerAnalyzer:
    def __init__(self, evidence, screen_corners=None):
        self.evidence = evidence
        self.size = tuple(evidence.metadata["size"])
        self.layouts = evidence.metadata["layouts"]
        self.reader = OpenCVReader()
        self.transform = None
        self.fixed_transform = screen_corners is not None
        if screen_corners is not None:
            corners = np.float32(screen_corners).reshape(4, 2)
            if not np.isfinite(corners).all() or not cv.isContourConvex(corners) or abs(cv.contourArea(corners)) < 16:
                raise ValueError("Screen corners must form a finite, non-degenerate TL TR BR BL quadrilateral")
            self.transform = cv.getPerspectiveTransform(
                corners, rectangle_points((0, 0, *self.size)))

    def _read(self, gray, transform, reference_ms, panel_reads=None):
        canvas = cv.warpPerspective(gray, transform, self.size)
        inverse = np.linalg.inv(transform)
        observations, outlined = [], []
        mismatches = 0
        for corner, layout in enumerate(self.layouts):
            if indicator_present(canvas, layout, self.evidence.indicator_style, self.evidence.indicator_width):
                outlined.append(corner)
            codes, count = panel_reads[corner] if panel_reads is not None else scan_panel(canvas, layout, self.reader)
            if not codes:
                continue
            points = cv.perspectiveTransform(rectangle_points(layout["bars"])[None], inverse)[0].tolist()
            for code in codes:
                row = self.evidence.lookup(code, reference_ms)
                matched = row is not None and row["corner"] == corner
                mismatches += not matched
                observations.append({
                    "corner": corner, "code": code, "points": points,
                    "method": "opencv_rectified_bands",
                    "valid_scanlines": count, "transition": len(codes) > 1,
                    "timestamp_ms": None if row is None else row["marker_ns"] // 1_000_000,
                    "display_index": row["index"] if matched and count >= 2 else None,
                })
        if mismatches:
            return {"selection": "ambiguous", "reason": "registration_or_journal_mismatch",
                    "screen_ns": None, "observations": observations, "outlined_corners": outlined}
        result = select_marker(observations, outlined, self.evidence)
        if result["screen_ns"] is not None and result["expected_empty_corners"]:
            # The empty quadrant is optical evidence too: bright fragments in
            # it can be a partly drawn next marker even when no EAN decodes.
            levels = []
            for item in observations:
                if item.get("display_index") is not None:
                    x, y, width, height = self.layouts[item["corner"]]["bars"]
                    levels.append(np.percentile(canvas[y:y + height:4, x:x + width:4], (10, 90)))
            dark, light = np.median(levels, axis=0)
            threshold = max(dark + 25, dark + 0.4 * (light - dark))
            unexpected = []
            for corner in result["expected_empty_corners"]:
                x, y, width, height = self.layouts[corner]["area"]
                # Ignore the quadrant boundary, where neighboring scanout and
                # registration error may leak a few pixels from another slot.
                interior = canvas[y + 8:y + height - 8:4, x + 8:x + width - 8:4]
                if interior.size and np.mean(interior > threshold) > 0.02:
                    unexpected.append(corner)
            if unexpected:
                result.update(selection="ambiguous", reason="expected_blank_quadrant_has_content",
                              screen_ns=None, unexpected_nonempty_corners=unexpected)
        return result

    def _transforms(self, gray):
        # Close bar-width dark runs for geometry discovery only. The decoder
        # always receives original grayscale pixels, excluding the indicator.
        small = gray
        seen = set()
        candidates = []
        thresholds = sorted(set([180, 210, 235, *map(int, np.percentile(small, (95, 98, 99.5)))]), reverse=True)
        for threshold in thresholds:
            _, mask = cv.threshold(small, threshold, 255, cv.THRESH_BINARY)
            if self.evidence.indicator_style == "underline":
                mask = cv.morphologyEx(mask, cv.MORPH_CLOSE,
                                      np.ones((1, max(7, gray.shape[1] // 56)), np.uint8))
            contours, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                area = cv.contourArea(contour)
                if area < small.size * 0.005:
                    continue
                polygon = cv.approxPolyDP(contour, 0.015 * cv.arcLength(contour, True), True)
                if len(polygon) != 4 or not cv.isContourConvex(polygon):
                    continue
                points = ordered_quad(polygon)
                if points is None:
                    continue
                key = tuple((points.ravel() / 4).astype(int))
                if key not in seen:
                    seen.add(key)
                    candidates.append((area, points))
        for _area, points in sorted(candidates, key=lambda item: item[0], reverse=True)[:12]:
            for layout in self.layouts:
                target = "outline" if self.evidence.indicator_style == "outline" else "barcode"
                yield cv.getPerspectiveTransform(points, rectangle_points(layout[target]))

    def analyze(self, frame, reference_ms):
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY)
        if self.transform is not None:
            result = self._read(gray, self.transform, reference_ms)
            if self.fixed_transform or result["screen_ns"] is not None:
                return result
            # A registered raster transition is evidence, not a reason to fit
            # a new transform until a different marker accidentally looks valid.
            if (result["observations"] and result["reason"] != "registration_or_journal_mismatch"
                    and (result["outlined_corners"] or result["reason"] == "differing_valid_band_values")):
                return result
        candidates = []
        for transform in self._transforms(gray):
            result = self._read(gray, transform, reference_ms)
            score = len({item["corner"] for item in result["observations"]
                         if item.get("display_index") is not None})
            if score and result["reason"] != "registration_or_journal_mismatch":
                candidates.append((result, transform, score))
        if not candidates:
            return {"selection": "ambiguous", "reason": "screen_registration_unavailable",
                    "screen_ns": None, "observations": [], "outlined_corners": []}
        best_score = max(item[2] for item in candidates)
        candidates = [item for item in candidates if item[2] == best_score]
        failed = next((item[0] for item in candidates if item[0]["screen_ns"] is None), None)
        if failed is not None:
            return failed
        choices = {(item[0]["display_index"], item[0]["selection"]) for item in candidates}
        if len(choices) != 1:
            return {"selection": "ambiguous", "reason": "conflicting_screen_registrations",
                    "screen_ns": None, "observations": [], "outlined_corners": []}
        result, self.transform, _score = candidates[0]
        return result


class ManualPanelAnalyzer:
    """Rectify each user-marked panel independently, without automatic discovery."""

    def __init__(self, evidence, quads, contrast=True, binary=False, valid_pixels=None):
        self.evidence = evidence
        self.quads = np.float32(quads).reshape(4, 4, 2)
        self.analyzer = MarkerAnalyzer(evidence, rectangle_points((0, 0, *evidence.metadata["size"])))
        self.contrast, self.binary = contrast, binary
        self.clahe = cv.createCLAHE(2, (8, 8))
        width, height = evidence.metadata["size"]
        self.maps = [np.zeros((height, width), np.float32) for _ in range(2)]
        self.inverses = []
        for quad, layout in zip(self.quads, evidence.metadata["layouts"]):
            inverse = cv.getPerspectiveTransform(rectangle_points(layout["barcode"]), quad)
            self.inverses.append(inverse)
            x, y, w, h = layout["area"]
            xx, yy = np.meshgrid(np.arange(x, x+w, dtype=np.float32), np.arange(y, y+h, dtype=np.float32))
            positions = cv.perspectiveTransform(np.stack((xx, yy), axis=-1).reshape(1, -1, 2), inverse).reshape(h, w, 2)
            self.maps[0][y:y+h, x:x+w] = positions[:, :, 0]
            self.maps[1][y:y+h, x:x+w] = positions[:, :, 1]
        self.valid_canvas = (cv.remap(np.float32(valid_pixels), *self.maps, cv.INTER_LINEAR) > .999
                             if valid_pixels is not None else None)

    def analyze(self, frame, reference_ms):
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        canvas = cv.remap(gray, *self.maps, cv.INTER_LINEAR, borderValue=0)
        variants = [("grayscale", gray)]
        if self.contrast:
            variants.append(("local_contrast", self.clahe.apply(gray)))
        regions = [(i, q[[3, 0, 1, 2]]) for i, q in enumerate(self.quads)]
        raw = []
        for name, pixels in variants:
            raw.extend({**s, "preprocessing": name, "location_source": "manual_region"}
                       for s in self.analyzer.reader.decode_regions(pixels, regions))
        if self.binary:
            raw.extend({**s, "preprocessing": "local_otsu", "location_source": "manual_region"}
                       for s in self.analyzer.reader.decode_regions(gray, regions, binary=True))
        by_corner = [{} for _ in range(4)]
        for symbol in raw:
            code = normalized_code(symbol["raw_code"], symbol["type"])
            if code:
                by_corner[symbol["region_corner"]].setdefault(code, set()).add(tuple(symbol["band"]))
        reads = [(sorted(codes), max(map(len, codes.values()), default=0)) for codes in by_corner]
        result = self.analyzer._read(canvas, np.eye(3), reference_ms, panel_reads=reads)
        # A missing/extrapolated underline is unknown, not a black/empty panel.
        ih, iw = gray.shape
        indicator_supported = []
        for layout in self.evidence.metadata["layouts"]:
            x, y, w, h = layout[self.evidence.indicator_style]
            # Include the neighboring pixels used to measure local contrast.
            if self.evidence.indicator_style == "underline":
                x, y, w, h = max(0, x-12), max(0, y-4), w+24, h+8
            mx, my = (m[y:y+h, x:x+w] for m in self.maps)
            supported = bool(((mx >= 0) & (mx <= iw-1) & (my >= 0) & (my <= ih-1)).all())
            if self.valid_canvas is not None:
                supported = supported and bool(self.valid_canvas[y:y+h, x:x+w].all())
            indicator_supported.append(supported)
        if not all(indicator_supported):
            result["reason_before_geometry_check"] = result["reason"]
            result.update(selection="ambiguous", screen_ns=None, reason="current_indicator_outside_image")
        result["unsupported_indicator_corners"] = [i for i, supported in enumerate(indicator_supported) if not supported]
        for item in result["observations"]:
            item["points"] = cv.perspectiveTransform(np.float32(item["points"])[None], self.inverses[item["corner"]])[0].tolist()
            item["method"] = "opencv_manual_bands"
        result["raw_observations"] = raw
        result["registration"] = "four_manual_undistorted_panels"
        return result
