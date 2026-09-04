#!/usr/bin/env python3
"""Display and inspect one QR calibration recording with QReader."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from queue import Empty, Queue
import statistics
import threading
import tkinter as tk
from tkinter import ttk

import cv2
import numpy as np
from PIL import Image, ImageTk

from calibration.display import DISPLAY_JOURNAL_NAME, timing_issues
from calibration.qr import (
    create_qreader,
    decode_qrs_with_quadrant_retries,
    order_by_quadrant,
    timestamp_payload,
)
from processing.recording.paths import IMAGE_DIRECTORY_NAME, resolve_recording_file


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INTRINSICS = PROJECT_ROOT / "calibration" / "intrinsics.json"
CAMERA_JOURNALS = ("camera_timestamps.jsonl", "camera_timestamps.json")
QUADRANT_COLORS = ("#47d9ff", "#ffc857", "#b8ee69", "#ed9cff")
LATEST_COLOR = "#fff176"
QUADRANT_NAMES = ("Top-left", "Top-right", "Bottom-right", "Bottom-left")


def read_json_rows(path: Path) -> list[dict]:
    if path.suffix == ".jsonl":
        rows = []
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, 1):
                if line.strip():
                    try:
                        rows.append(json.loads(line))
                    except json.JSONDecodeError as error:
                        raise ValueError(f"Invalid JSON in {path.name}, line {line_number}") from error
        return rows
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    for key in ("frames", "camera_timestamps"):
        if isinstance(data.get(key), list):
            return data[key]
    raise ValueError(f"{path.name} does not contain a frame list")


def payload_time(raw: str | None) -> str:
    if raw is None or not raw.isdigit() or len(raw) != 12:
        return "Unreadable"
    milliseconds = int(raw)
    seconds, remainder = divmod(milliseconds, 1000)
    return f"{seconds:,}".replace(",", " ") + f".{remainder:03d} s"


def editable_seconds(value: int | None) -> str:
    if value is None:
        return ""
    seconds, nanoseconds = divmod(int(value), 1_000_000_000)
    return f"{seconds}.{nanoseconds:09d}"


def parse_seconds(value: str, label: str) -> int | None:
    text = value.strip()
    if not text:
        return None
    try:
        return int(Decimal(text) * 1_000_000_000)
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{label} must be seconds written as a number") from error


def parse_qr_value(value: str, quadrant: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if not text.isdigit() or len(text) > 12:
        raise ValueError(f"{quadrant} QR must be the integer milliseconds stored in the QR")
    return text.zfill(12)


class Undistorter:
    def __init__(self, path: Path):
        data = json.loads(path.read_text(encoding="utf-8"))
        self.matrix = np.asarray(data["camera_matrix"], dtype=np.float64)
        self.distortion = np.asarray(data["dist_coeffs"], dtype=np.float64).reshape(-1)
        self.calibration_size = tuple(data["image_size"]) if data.get("image_size") else None
        if (
            self.matrix.shape != (3, 3)
            or not np.isfinite(self.matrix).all()
            or not np.isfinite(self.distortion).all()
            or self.distortion.size not in (4, 5, 8, 12, 14)
        ):
            raise ValueError("Invalid camera_matrix or dist_coeffs in intrinsics.json")
        self.maps: dict[tuple[tuple[int, int], float], tuple] = {}

    def geometry(self, size: tuple[int, int], alpha: float) -> tuple:
        key = size, alpha
        if key not in self.maps:
            matrix = self.matrix.copy()
            if self.calibration_size:
                matrix[0, :] *= size[0] / self.calibration_size[0]
                matrix[1, :] *= size[1] / self.calibration_size[1]
            output, _ = cv2.getOptimalNewCameraMatrix(
                matrix, self.distortion, size, alpha, size
            )
            maps = cv2.initUndistortRectifyMap(
                matrix, self.distortion, None, output, size, cv2.CV_32FC1
            )
            self.maps[key] = matrix, output, maps
        return self.maps[key]

    def image(self, frame: np.ndarray, alpha: float) -> np.ndarray:
        size = (frame.shape[1], frame.shape[0])
        _, _, maps = self.geometry(size, alpha)
        return cv2.remap(frame, *maps, cv2.INTER_LINEAR)

    def to_original(self, points: np.ndarray, size: tuple[int, int], alpha: float) -> np.ndarray:
        matrix, output, _ = self.geometry(size, alpha)
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        homogeneous = np.column_stack((points, np.ones(len(points))))
        rays = homogeneous @ np.linalg.inv(output).T
        return cv2.projectPoints(
            rays, np.zeros(3), np.zeros(3), matrix, self.distortion
        )[0].reshape(-1, 2)


class DisplayTimeline:
    def __init__(self, folder: Path):
        path = folder / DISPLAY_JOURNAL_NAME
        if not path.is_file():
            raise ValueError(f"Missing {DISPLAY_JOURNAL_NAME}")
        rows = read_json_rows(path)
        self.metadata = next((row for row in rows if row.get("kind") == "session"), None)
        self.frames = [row for row in rows if row.get("kind") == "frame"]
        if self.metadata is None or not self.frames:
            raise ValueError("Display timing journal has no session or frame entries")
        self.paused = {
            row.get("last_frame_index")
            for row in rows
            if row.get("kind") == "pause" and row.get("paused")
        }
        self.by_payload: dict[str, list[dict]] = {}
        previous = -1
        for position, frame in enumerate(self.frames):
            if frame.get("index") != position or int(frame.get("marker_ns", -1)) <= previous:
                raise ValueError("Display journal frames are missing, reordered, or non-monotonic")
            previous = int(frame["marker_ns"])
            self.by_payload.setdefault(timestamp_payload(previous), []).append(frame)

    def match(self, raw: str | None, reference_ns: int | None) -> dict | None:
        if raw is None or len(raw) != 12 or not raw.isdigit():
            return None
        candidates = self.by_payload.get(raw, [])
        if len(candidates) == 1:
            return candidates[0]
        if candidates and reference_ns is not None:
            return min(candidates, key=lambda row: abs(int(row["marker_ns"]) - reference_ns))
        return None

    def marker_status(self, index: int) -> tuple[str, list[str]]:
        current = self.frames[index]
        following = self.frames[index + 1] if index + 1 < len(self.frames) else None
        issues = ["marker_" + issue for issue in timing_issues(current)]
        if index in self.paused:
            issues.append("marker_held_for_pause")
        if following is None:
            issues.append("replacement_evidence_missing")
        else:
            issues.extend("replacement_" + issue for issue in timing_issues(following))
            if following.get("interval_ns") is None:
                issues.append("replacement_interval_unavailable")
        if not issues:
            return "Clean", []
        unknown = {"replacement_evidence_missing", "replacement_interval_unavailable"}
        return ("Unknown" if all(issue in unknown for issue in issues) else "Timing suspect"), issues

    def totals(self) -> dict:
        return {
            "displayed": len(self.frames),
            "missed_period_candidates": sum(int(row.get("skipped_periods", 0)) for row in self.frames),
            "late_submissions": sum(bool(row.get("late_submit")) for row in self.frames),
            "irregular_intervals": sum(bool(row.get("irregular_interval")) for row in self.frames),
        }


class RecordingAnalyzer:
    def __init__(self, folder: Path, intrinsics: Path, reader=None):
        self.folder = folder.expanduser().resolve()
        if not self.folder.is_dir():
            raise ValueError("Select an existing calibration recording folder")
        journal = next((self.folder / name for name in CAMERA_JOURNALS if (self.folder / name).is_file()), None)
        if journal is None:
            raise ValueError("Recording has no camera timestamp journal")
        raw_rows = read_json_rows(journal)
        self.rows = []
        seen = set()
        for row in raw_rows:
            filename = row.get("frame") or row.get("camera_frame")
            if not isinstance(filename, str) or not filename or filename in seen:
                raise ValueError("Camera journal contains missing or duplicate frame names")
            path = resolve_recording_file(self.folder, filename, IMAGE_DIRECTORY_NAME)
            if path is None:
                raise ValueError("Missing image or image outside recording folder: " + filename)
            seen.add(filename)
            self.rows.append({**row, "filename": filename})
        if not self.rows:
            raise ValueError("Camera timestamp journal is empty")
        self.timeline = DisplayTimeline(self.folder)
        session_path = self.folder / "camera_timing_session.json"
        self.session = json.loads(session_path.read_text(encoding="utf-8")) if session_path.is_file() else {}
        self.epochs = {
            entry.get("stream_epoch"): entry
            for entry in self.session.get("epochs", [])
            if entry.get("stream_epoch") is not None
        }
        if not intrinsics.is_file():
            raise ValueError("Camera intrinsics file does not exist: " + str(intrinsics))
        self.undistorter = Undistorter(intrinsics)
        self.reader = reader or create_qreader()
        self.cache: dict[tuple[int, float], dict] = {}
        self.manual_values: dict[int, dict] = {}

    def pts_monotonic_ns(self, row: dict) -> int | None:
        for key in ("frame_monotonic_ns", "captured_monotonic_ns"):
            if row.get(key) is not None:
                return int(row[key])
        epoch = self.epochs.get(row.get("stream_epoch"))
        if epoch and row.get("pts_ns") is not None and epoch.get("pipeline_zero_monotonic_ns") is not None:
            return int(epoch["pipeline_zero_monotonic_ns"]) + int(row["pts_ns"])
        return None

    def pts_monotonic_for_value(self, row: dict, pts_ns: int | None) -> int | None:
        if pts_ns == row.get("pts_ns"):
            return self.pts_monotonic_ns(row)
        epoch = self.epochs.get(row.get("stream_epoch"))
        if pts_ns is not None and epoch and epoch.get("pipeline_zero_monotonic_ns") is not None:
            return int(epoch["pipeline_zero_monotonic_ns"]) + int(pts_ns)
        return None

    @staticmethod
    def detected_qr_values(result: dict) -> tuple[str | None, ...]:
        by_quadrant = [[] for _ in range(4)]
        for item in result["observations"]:
            if item["raw"] is not None:
                by_quadrant[item["quadrant"]].append(item["raw"])
        return tuple(values[0] if len(values) == 1 else None for values in by_quadrant)

    def frame_values(self, result: dict) -> dict:
        if result["index"] in self.manual_values:
            return self.manual_values[result["index"]]
        row = result["row"]
        return {
            "pts_ns": row.get("pts_ns"),
            "ntp_ns": row.get("reference_ntp_ns") or row.get("reference_timestamp_normalized_ns"),
            "qrs": self.detected_qr_values(result),
            "manual": False,
        }

    def set_manual_values(self, index: int, values: dict) -> None:
        self.manual_values[index] = {**values, "manual": True}

    def reset_manual_values(self, index: int) -> None:
        self.manual_values.pop(index, None)

    @property
    def output_folder(self) -> Path:
        return self.folder.with_name(self.folder.name + "_analysis")

    def check_frame(self, result: dict) -> dict:
        values = self.frame_values(result)
        qrs = values["qrs"]
        for quadrant, raw in zip(QUADRANT_NAMES, qrs):
            if raw is None:
                return {
                    "valid": False,
                    "skippable": True,
                    "reason": f"Missing or multiple {quadrant} QR values",
                    "values": values,
                }
            if not raw.isdigit() or len(raw) != 12:
                return {"valid": False, "reason": f"Invalid {quadrant} QR value: {raw}", "values": values}
        reference_ns = self.pts_monotonic_for_value(result["row"], values["pts_ns"])
        markers = []
        for quadrant, raw in enumerate(qrs):
            marker = self.timeline.match(raw, reference_ns)
            if marker is None:
                return {"valid": False, "reason": f"{QUADRANT_NAMES[quadrant]} QR is not in the display journal", "values": values}
            if marker["corner"] != quadrant:
                return {
                    "valid": False,
                    "reason": f"{QUADRANT_NAMES[quadrant]} contains a QR recorded for {QUADRANT_NAMES[marker['corner']]}",
                    "values": values,
                }
            markers.append(marker)
        indices = [int(marker["index"]) for marker in markers]
        ordered = sorted(indices)
        if len(set(indices)) != 4 or ordered != list(range(ordered[0], ordered[0] + 4)):
            return {
                "valid": False,
                "reason": "The four QR timings are not one consecutive clockwise sequence",
                "values": values,
                "indices": indices,
            }
        latest_quadrant = max(range(4), key=lambda quadrant: indices[quadrant])
        latest_marker = markers[latest_quadrant]
        timing_status, issues = self.timeline.marker_status(latest_marker["index"])
        offset_ms = None
        if reference_ns is not None:
            offset_ms = (reference_ns - int(latest_marker["marker_ns"])) / 1e6
        return {
            "valid": True,
            "reason": None,
            "skippable": False,
            "values": values,
            "indices": indices,
            "latest_quadrant": latest_quadrant,
            "latest_raw": qrs[latest_quadrant],
            "latest_marker": latest_marker,
            "timing_status": timing_status,
            "issues": issues,
            "offset_ms": offset_ms,
        }

    def analyze(self, index: int, alpha: float = 0.25) -> dict:
        key = index, alpha
        if key in self.cache:
            return self.cache[key]
        row = self.rows[index]
        image_path = resolve_recording_file(
            self.folder, row["filename"], IMAGE_DIRECTORY_NAME
        )
        if image_path is None:
            raise ValueError("Missing image or image outside recording folder: " + row["filename"])
        original = cv2.imread(str(image_path))
        if original is None:
            raise ValueError("Could not read " + row["filename"])
        undistorted = self.undistorter.image(original, alpha)
        detections = order_by_quadrant(
            decode_qrs_with_quadrant_retries(self.reader, undistorted),
            (undistorted.shape[1], undistorted.shape[0]),
        )
        reference_ns = self.pts_monotonic_ns(row)
        observations = []
        for detection in detections:
            x1, y1, x2, y2 = detection["bbox"]
            undistorted_box = np.asarray(((x1, y1), (x2, y1), (x2, y2), (x1, y2)))
            original_box = self.undistorter.to_original(
                undistorted_box, (original.shape[1], original.shape[0]), alpha
            )
            marker = self.timeline.match(detection["raw"], reference_ns)
            status, issues = ("Unmatched", ["no_display_journal_match"])
            timing_status = status
            offset_ms = None
            mismatch = False
            if marker is not None:
                mismatch = marker["corner"] != detection["quadrant"]
                status, issues = self.timeline.marker_status(marker["index"])
                timing_status = status
                if mismatch:
                    status = "Quadrant mismatch"
                    issues = ["decoded_quadrant_does_not_match_journal", *issues]
                if reference_ns is not None:
                    offset_ms = (reference_ns - int(marker["marker_ns"])) / 1e6
            observations.append({
                **detection,
                "marker": marker,
                "display_index": marker.get("index") if marker else None,
                "status": status,
                "timing_status": timing_status,
                "issues": issues,
                "mismatch": mismatch,
                "offset_ms": offset_ms,
                "undistorted_points": undistorted_box,
                "original_points": original_box,
            })
        matched = [item for item in observations if item["display_index"] is not None]
        latest = max(matched, key=lambda item: item["display_index"], default=None)
        result = {
            "index": index,
            "row": row,
            "original": original,
            "undistorted": undistorted,
            "observations": observations,
            "latest": latest,
            "pts_monotonic_ns": reference_ns,
        }
        self.cache[key] = result
        return result

    def summarize(self, alpha: float, cancel: threading.Event, progress) -> dict:
        counts = Counter()
        clean_offsets = []
        frame_reports = []
        processed = 0
        stopped = None
        for index in range(len(self.rows)):
            if cancel.is_set():
                break
            result = self.analyze(index, alpha)
            processed += 1
            check = self.check_frame(result)
            observations = result["observations"]
            counts["detections"] += len(observations)
            counts["unreadable"] += sum(item["raw"] is None for item in observations)
            counts["mismatches"] += sum(item["mismatch"] for item in observations)
            counts["timing_suspect"] += sum(
                item["timing_status"] == "Timing suspect" for item in observations
            )
            values = check["values"]
            if check["valid"]:
                validation = "accepted_" + check["timing_status"].lower().replace(" ", "_")
            elif check.get("skippable"):
                validation = "skipped_incomplete"
            else:
                validation = "stopped_invalid"
            frame_reports.append({
                "frame_number": index + 1,
                "filename": result["row"]["filename"],
                "validation": validation,
                "reason": check["reason"],
                "manual_values": bool(values["manual"]),
                "pts_ns": values["pts_ns"],
                "ntp_ns": values["ntp_ns"],
                "qr_top_left_ms": values["qrs"][0],
                "qr_top_right_ms": values["qrs"][1],
                "qr_bottom_right_ms": values["qrs"][2],
                "qr_bottom_left_ms": values["qrs"][3],
                "decoded_detections": len(observations),
                "display_indices": check.get("indices", []),
                "latest_qr_ms": check.get("latest_raw"),
                "latest_quadrant": (
                    QUADRANT_NAMES[check["latest_quadrant"]]
                    if check.get("latest_quadrant") is not None else None
                ),
                "latest_display_index": (
                    check["latest_marker"]["index"] if check.get("latest_marker") else None
                ),
                "timing_status": check.get("timing_status"),
                "timing_issues": check.get("issues", []),
                "pts_minus_latest_qr_ms": check.get("offset_ms"),
            })
            progress(processed, len(self.rows))
            if not check["valid"]:
                if check.get("skippable"):
                    counts["skipped_incomplete_frames"] += 1
                    continue
                stopped = {"index": index, "reason": check["reason"]}
                break
            counts["accepted_frames"] += 1
            if check["timing_status"] == "Clean" and check["offset_ms"] is not None:
                clean_offsets.append(check["offset_ms"])
        return {
            "recording_directory": str(self.folder),
            "analysis_alpha": alpha,
            "processed": processed,
            "total": len(self.rows),
            "cancelled": cancel.is_set() and stopped is None,
            "stopped": stopped,
            "counts": dict(counts),
            "clean_offsets": len(clean_offsets),
            "median_offset_ms": statistics.median(clean_offsets) if clean_offsets else None,
            "display": self.timeline.totals(),
            "frames": frame_reports,
        }

    def save_report(self, report: dict) -> dict:
        output = self.output_folder
        output.mkdir(parents=False, exist_ok=True)
        json_path = output / "calibration_analysis.json"
        csv_path = output / "calibration_frames.csv"
        saved = {
            **report,
            "generated_at": datetime.now().astimezone().isoformat(),
            "output_directory": str(output),
            "report_files": [json_path.name, csv_path.name],
        }
        json_path.write_text(json.dumps(saved, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        frames = saved["frames"]
        if frames:
            with csv_path.open("w", encoding="utf-8", newline="") as destination:
                writer = csv.DictWriter(destination, fieldnames=frames[0].keys())
                writer.writeheader()
                for frame in frames:
                    writer.writerow({
                        key: json.dumps(value, separators=(",", ":"))
                        if isinstance(value, (list, dict)) else value
                        for key, value in frame.items()
                    })
        else:
            csv_path.write_text("", encoding="utf-8")
        return saved


class AnalysisWorker(threading.Thread):
    def __init__(self, model: RecordingAnalyzer):
        super().__init__(daemon=True, name="qr-calibration-analysis")
        self.model = model
        self.jobs: Queue = Queue()
        self.results: Queue = Queue()
        self.cancel = threading.Event()

    def run(self) -> None:
        while True:
            job = self.jobs.get()
            if job[0] == "stop":
                return
            kind, request, payload = job
            try:
                if kind == "frame":
                    result = self.model.analyze(payload["index"], payload["alpha"])
                    self.results.put((kind, request, result))
                elif kind == "scan":
                    self.cancel.clear()
                    result = self.model.summarize(
                        payload["alpha"], self.cancel,
                        lambda done, total: self.results.put(("progress", request, (done, total))),
                    )
                    result = self.model.save_report(result)
                    self.results.put((kind, request, result))
            except Exception as error:
                self.results.put(("error", request, str(error)))

    def submit(self, kind: str, request: int, **payload) -> None:
        if kind == "frame":
            self.cancel.set()
        self.jobs.put((kind, request, payload))

    def stop(self) -> None:
        self.cancel.set()
        self.jobs.put(("stop", 0, {}))


class CalibrationWindow:
    def __init__(self, root: tk.Tk, model: RecordingAnalyzer, alpha: float = 0.25):
        self.root = root
        self.model = model
        self.worker = AnalysisWorker(model)
        self.worker.start()
        self.index = 0
        self.request = 0
        self.scan_request = 0
        self.current = None
        self.photo = None
        self.resize_job = None
        self.current_check = None
        self.saved_output: Path | None = None
        self.variant = tk.StringVar(value="Undistorted")
        self.alpha = tk.StringVar(value=f"{alpha:g}")
        self.show_all_times = tk.BooleanVar(value=True)
        self.draw_all_boxes = tk.BooleanVar(value=True)
        self.position = tk.StringVar(value="1")
        self.title = tk.StringVar(value="Loading first frame…")
        self.pts_edit = tk.StringVar()
        self.ntp_edit = tk.StringVar()
        self.qr_edits = [tk.StringVar() for _ in range(4)]
        self.exhibited = tk.StringVar(value="LATEST EXHIBITED TIME\nLoading…")
        self.codes = tk.StringVar(value="")
        self.status = tk.StringVar(value="QReader uses the undistorted image at alpha 0.25.")
        self.summary = tk.StringVar(value="Folder analysis starts automatically.")
        self.scan_progress = tk.StringVar(value=f"Analysis: 0 / {len(model.rows)}")
        self._build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Left>", lambda event: self._keyboard_step(event, -1))
        root.bind("<Right>", lambda event: self._keyboard_step(event, 1))
        root.after(50, self._poll)
        self.show_frame()
        root.after(150, self.scan)

    def _build(self) -> None:
        self.root.title("QR Calibration Analysis — " + self.model.folder.name)
        width = min(1480, self.root.winfo_screenwidth() - 60)
        height = min(980, self.root.winfo_screenheight() - 90)
        self.root.geometry(f"{max(900, width)}x{max(680, height)}")
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        navigation = ttk.Frame(outer)
        navigation.pack(fill="x")
        ttk.Button(navigation, text="Previous", command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(navigation, text="Next", command=lambda: self.step(1)).pack(side="left", padx=4)
        entry = ttk.Entry(navigation, textvariable=self.position, width=7)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _: self.go())
        ttk.Button(navigation, text="Go", command=self.go).pack(side="left", padx=4)
        self.slider = ttk.Scale(navigation, from_=1, to=len(self.model.rows))
        self.slider.pack(side="left", fill="x", expand=True, padx=6)
        self.slider.bind("<ButtonRelease-1>", lambda _: self.go(round(self.slider.get())))
        ttk.Button(navigation, text="Create analysis files", command=self.scan).pack(side="left", padx=4)
        ttk.Button(navigation, text="Cancel", command=self.worker.cancel.set).pack(side="left")
        ttk.Label(
            navigation, textvariable=self.scan_progress, anchor="e",
            font=("TkDefaultFont", 10, "bold"),
        ).pack(side="right", padx=(12, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(6, 4))
        ttk.Label(controls, text="Image").pack(side="left")
        image = ttk.Combobox(
            controls, textvariable=self.variant,
            values=("Undistorted", "Original"), state="readonly", width=12,
        )
        image.pack(side="left", padx=(4, 12))
        image.bind("<<ComboboxSelected>>", lambda _: self.populate() if self.current else None)
        ttk.Label(controls, text="Alpha").pack(side="left")
        alpha = ttk.Combobox(
            controls, textvariable=self.alpha,
            values=("0", "0.25", "0.5", "0.75", "1"), state="readonly", width=5,
        )
        alpha.pack(side="left", padx=(4, 12))
        alpha.bind("<<ComboboxSelected>>", lambda _: self.show_frame())
        ttk.Checkbutton(
            controls, text="Show all decoded times", variable=self.show_all_times, command=self.draw
        ).pack(side="left", padx=6)
        ttk.Checkbutton(
            controls, text="Draw all QR boxes", variable=self.draw_all_boxes, command=self.draw
        ).pack(side="left", padx=6)

        ttk.Label(outer, textvariable=self.title, font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        information = ttk.LabelFrame(outer, text="Editable timing values", padding=6)
        information.pack(fill="x", pady=5)
        ttk.Label(information, text="PTS (seconds)").grid(row=0, column=0, sticky="w")
        ttk.Entry(information, textvariable=self.pts_edit).grid(row=1, column=0, sticky="ew", padx=(0, 4))
        ttk.Label(information, text="NTP Unix time (seconds)").grid(row=0, column=1, sticky="w")
        ttk.Entry(information, textvariable=self.ntp_edit).grid(row=1, column=1, sticky="ew", padx=4)
        for column, (name, variable) in enumerate(zip(QUADRANT_NAMES, self.qr_edits), start=2):
            ttk.Label(information, text=name + " QR (ms)").grid(row=0, column=column, sticky="w")
            ttk.Entry(information, textvariable=variable, width=15).grid(
                row=1, column=column, sticky="ew", padx=4
            )
        ttk.Button(information, text="APPLY AND CONTINUE", command=self.apply_edits).grid(
            row=1, column=6, padx=(8, 4)
        )
        ttk.Button(information, text="RESTORE DETECTED", command=self.restore_detected).grid(
            row=1, column=7, padx=(4, 0)
        )
        for column in range(6):
            information.columnconfigure(column, weight=1)
        style = ttk.Style(self.root)
        style.configure("Latest.TLabel", background=LATEST_COLOR, foreground="#191600")
        ttk.Label(
            outer, textvariable=self.exhibited, padding=8, anchor="center", style="Latest.TLabel"
        ).pack(fill="x")
        ttk.Label(outer, textvariable=self.codes, wraplength=1350).pack(anchor="w", fill="x")
        self.canvas = tk.Canvas(outer, background="#15191e", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, pady=5)
        self.canvas.bind("<Configure>", self.schedule_draw)
        ttk.Label(outer, textvariable=self.summary, wraplength=1350).pack(anchor="w")
        ttk.Label(outer, textvariable=self.status, wraplength=1350).pack(anchor="w", pady=(3, 0))

    def _keyboard_step(self, event, delta: int):
        if event.widget.winfo_class() not in ("Entry", "TEntry", "TCombobox"):
            self.step(delta)
            return "break"

    def step(self, delta: int) -> None:
        self.go(self.index + 1 + delta)

    def go(self, number=None) -> None:
        try:
            index = int(self.position.get() if number is None else number) - 1
            if not 0 <= index < len(self.model.rows):
                raise ValueError
        except ValueError:
            self.status.set(f"Choose a frame from 1 to {len(self.model.rows)}.")
            return
        self.index = index
        self.show_frame()

    def show_frame(self) -> None:
        self.request += 1
        self.current = None
        self.current_check = None
        self.position.set(str(self.index + 1))
        self.slider.set(self.index + 1)
        filename = self.model.rows[self.index]["filename"]
        self.title.set(f"{filename} — {self.index + 1} / {len(self.model.rows)} — decoding…")
        self.status.set("QReader is decoding the undistorted image.")
        self.canvas.delete("all")
        self.worker.submit("frame", self.request, index=self.index, alpha=float(self.alpha.get()))

    def scan(self) -> None:
        self.scan_request += 1
        self.summary.set("Analyzing recording…")
        total = len(self.model.rows)
        self.scan_progress.set(f"Analysis: 0 / {total} · {total} remaining")
        self.worker.submit("scan", self.scan_request, alpha=float(self.alpha.get()))

    def apply_edits(self) -> None:
        if self.current is None:
            return
        try:
            values = {
                "pts_ns": parse_seconds(self.pts_edit.get(), "PTS"),
                "ntp_ns": parse_seconds(self.ntp_edit.get(), "NTP"),
                "qrs": tuple(
                    parse_qr_value(variable.get(), quadrant)
                    for variable, quadrant in zip(self.qr_edits, QUADRANT_NAMES)
                ),
            }
        except ValueError as error:
            self.status.set(str(error))
            return
        self.model.set_manual_values(self.index, values)
        self.populate()
        if self.current_check["valid"]:
            self.scan()

    def restore_detected(self) -> None:
        if self.current is None:
            return
        self.model.reset_manual_values(self.index)
        self.populate()

    def _poll(self) -> None:
        try:
            while True:
                kind, request, payload = self.worker.results.get_nowait()
                if kind == "frame" and request == self.request:
                    self.current = payload
                    self.populate()
                elif kind == "progress" and request == self.scan_request:
                    done, total = payload
                    progress = f"Analysis: {done} / {total} · {max(0, total - done)} remaining"
                    self.scan_progress.set(progress)
                    self.summary.set(progress)
                elif kind == "scan" and request == self.scan_request:
                    self.show_summary(payload)
                elif kind == "error" and request in (self.request, self.scan_request):
                    self.status.set(payload)
                    if request == self.scan_request:
                        self.scan_progress.set("Analysis stopped with an error")
        except Empty:
            pass
        self.root.after(50, self._poll)

    def populate(self) -> None:
        result = self.current
        row = result["row"]
        check = self.model.check_frame(result)
        values = check["values"]
        self.current_check = check
        self.title.set(
            f"{row['filename']} — {result['index'] + 1} / {len(self.model.rows)} — "
            f"{self.variant.get()}"
        )
        self.pts_edit.set(editable_seconds(values["pts_ns"]))
        self.ntp_edit.set(editable_seconds(values["ntp_ns"]))
        for variable, raw in zip(self.qr_edits, values["qrs"]):
            variable.set(raw or "")
        if not check["valid"]:
            prefix = "Frame skipped" if check.get("skippable") else "Validation stopped"
            self.exhibited.set(f"LATEST EXHIBITED TIME\n{prefix}: {check['reason']}")
        else:
            offset = "" if check["offset_ms"] is None else (
                f" · camera PTS minus displayed QR {check['offset_ms']:.3f} ms"
            )
            self.exhibited.set(
                f"LATEST VALID DISPLAYED QR TIME\n{payload_time(check['latest_raw'])} · "
                f"{QUADRANT_NAMES[check['latest_quadrant']]} · {check['timing_status']}{offset}"
            )
        code_lines = [
            f"{name}: {payload_time(raw)}"
            for name, raw in zip(QUADRANT_NAMES, values["qrs"])
        ]
        self.codes.set("   |   ".join(code_lines) if code_lines else "No QR code detected.")
        if not check["valid"]:
            source = "manual values" if values["manual"] else "QReader detections"
            action = "Skipped" if check.get("skippable") else "Stopped"
            self.status.set(f"{action} on {source}: {check['reason']}")
        else:
            self.status.set(
                ("Manual values accepted. " if values["manual"] else "Four QReader values accepted. ")
                + "Latest display timing: " + (
                    ", ".join(check["issues"])
                    if check["issues"] else "clean."
                )
            )
        self.draw()

    def show_summary(self, report: dict) -> None:
        counts = report["counts"]
        state = "Cancelled" if report["cancelled"] else "Complete"
        if report["stopped"]:
            state = f"Stopped at frame {report['stopped']['index'] + 1}: {report['stopped']['reason']}"
        self.saved_output = (
            Path(report["output_directory"])
            if not report["cancelled"]
            and report["stopped"] is None
            and report["processed"] == report["total"]
            else None
        )
        remaining = max(0, report["total"] - report["processed"])
        self.scan_progress.set(
            f"{state}: {report['processed']} / {report['total']} · {remaining} remaining"
        )
        offset = (
            "no clean offset"
            if report["median_offset_ms"] is None
            else (
                "median camera PTS minus latest valid displayed QR time "
                f"{report['median_offset_ms']:.3f} ms (n={report['clean_offsets']})"
            )
        )
        display = report["display"]
        self.summary.set(
            f"{state}: {report['processed']} / {report['total']} frames; {offset}. "
            f"QR detections {counts.get('detections', 0)}, unreadable {counts.get('unreadable', 0)}, "
            f"incomplete frames skipped {counts.get('skipped_incomplete_frames', 0)}, "
            f"quadrant mismatches {counts.get('mismatches', 0)}, timing-suspect markers "
            f"{counts.get('timing_suspect', 0)}. Display: {display['late_submissions']} late, "
            f"{display['irregular_intervals']} irregular, "
            f"{display['missed_period_candidates']} missed-period candidates. "
            f"Results saved in {report['output_directory']}"
        )
        self.status.set("Saved calibration_analysis.json and calibration_frames.csv in "
                        + report["output_directory"])
        if report["stopped"]:
            self.root.after(0, lambda: self.go(report["stopped"]["index"] + 1))

    def schedule_draw(self, _event=None) -> None:
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(60, self.draw)

    def draw(self) -> None:
        self.resize_job = None
        self.canvas.delete("all")
        if self.current is None:
            return
        pixels = self.current["undistorted" if self.variant.get() == "Undistorted" else "original"]
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        scale = min(width / pixels.shape[1], height / pixels.shape[0])
        size = max(1, int(pixels.shape[1] * scale)), max(1, int(pixels.shape[0] * scale))
        image = Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB)).resize(size, Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(image)
        left, top = (width - size[0]) / 2, (height - size[1]) / 2
        self.canvas.create_image(left, top, image=self.photo, anchor="nw")
        check = self.current_check or self.model.check_frame(self.current)
        latest_quadrant = check.get("latest_quadrant") if check["valid"] else None
        values = check["values"]
        for item in self.current["observations"]:
            is_latest = item["quadrant"] == latest_quadrant
            if not is_latest and not self.draw_all_boxes.get() and not self.show_all_times.get():
                continue
            points = item[
                "undistorted_points" if self.variant.get() == "Undistorted" else "original_points"
            ] * scale + np.array([left, top])
            color = LATEST_COLOR if is_latest else QUADRANT_COLORS[item["quadrant"]]
            if is_latest or self.draw_all_boxes.get():
                self.canvas.create_polygon(
                    points.ravel().tolist(), fill="", outline=color, width=4 if is_latest else 2
                )
            if is_latest or self.show_all_times.get():
                text = ("LATEST · " if is_latest else "") + payload_time(values["qrs"][item["quadrant"]])
                label = self.canvas.create_text(
                    points[:, 0].min() + 4, max(4, points[:, 1].min() - 26), text=text, fill=color,
                    anchor="nw", font=("TkDefaultFont", 10, "bold"),
                )
                background = self.canvas.create_rectangle(self.canvas.bbox(label), fill="#15191e", outline=color)
                self.canvas.tag_raise(label, background)

    def close(self) -> None:
        self.worker.stop()
        self.root.destroy()


def run_recording_display(
    folder: Path,
    intrinsics: Path = DEFAULT_INTRINSICS,
    *,
    alpha: float = 0.25,
) -> Path | None:
    """Open the inspection window and return files created during this session."""
    model = RecordingAnalyzer(folder, intrinsics)
    root = tk.Tk()
    window = CalibrationWindow(root, model, alpha=alpha)
    root.mainloop()
    return window.saved_output


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze a QR calibration recording")
    parser.add_argument("folder", type=Path, help="Recording folder")
    parser.add_argument(
        "--intrinsics", type=Path, default=DEFAULT_INTRINSICS,
        help=f"Camera intrinsics JSON (default: {DEFAULT_INTRINSICS})",
    )
    arguments = parser.parse_args()
    run_recording_display(arguments.folder, arguments.intrinsics)


if __name__ == "__main__":
    main()
