"""Offline calibration inspection and coordinate-aware decoder comparisons."""

from collections import Counter, OrderedDict
import json
from pathlib import Path

import cv2 as cv
import numpy as np

from CALIBRATION.barcode_decoders import OpenCVReader, ZBarReader
from CALIBRATION.marker_analysis import DisplayEvidence, MarkerAnalyzer
from analyze_calibration_recording_offset import (
    load_epochs, load_manifest, normalize_ean13, normalize_timing_row,
    summarize, unwrap_monotonic_ms,
)

QUADRANTS = ("Top-left", "Top-right", "Bottom-right", "Bottom-left")


class Undistorter:
    def __init__(self, path, calibration_size=None):
        self.path = Path(path).expanduser().resolve()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.matrix = np.asarray(data["camera_matrix"], dtype=np.float64)
        self.distortion = np.asarray(data["dist_coeffs"], dtype=np.float64).reshape(-1)
        if (self.matrix.shape != (3, 3) or not np.isfinite(self.matrix).all()
                or self.matrix[0, 0] <= 0 or self.matrix[1, 1] <= 0
                or not np.allclose(self.matrix[2], [0, 0, 1])
                or self.distortion.size not in (4, 5, 8, 12, 14)
                or not np.isfinite(self.distortion).all()):
            raise ValueError("Invalid camera_matrix or dist_coeffs in the intrinsic file")
        size = calibration_size or data.get("image_size")
        self.calibration_size = tuple(map(int, size)) if size is not None else None
        if self.calibration_size and (len(self.calibration_size) != 2 or min(self.calibration_size) <= 0):
            raise ValueError("Calibration resolution must contain positive width and height")
        self.maps = {}

    def geometry(self, size):
        size = tuple(size)
        if size not in self.maps:
            matrix = self.matrix.copy()
            if self.calibration_size:
                matrix[0, :] *= size[0] / self.calibration_size[0]
                matrix[1, :] *= size[1] / self.calibration_size[1]
            # A specified output matrix avoids unstable full-field optimization
            # for strong radial models. The output retains the input dimensions.
            output = matrix.copy()
            output[0, 0] *= .75
            output[1, 1] *= .75
            output[0, 2], output[1, 2] = size[0] / 2, size[1] / 2
            maps = cv.initUndistortRectifyMap(matrix, self.distortion, None, output, size, cv.CV_32FC1)
            self.maps[size] = matrix, output, maps
        return self.maps[size]

    def image(self, frame):
        _, _, maps = self.geometry((frame.shape[1], frame.shape[0]))
        return cv.remap(frame, *maps, cv.INTER_LINEAR)

    def points(self, points, source, target, size):
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if source == target or not len(points):
            return points.copy()
        matrix, output, _ = self.geometry(size)
        if source == "Original":
            criteria = (cv.TERM_CRITERIA_COUNT | cv.TERM_CRITERIA_EPS, 40, 1e-8)
            iterative = getattr(cv, "undistortPointsIter", None)
            if iterative is not None:  # OpenCV 4.x exposes the overload separately.
                return iterative(points[:, None], matrix, self.distortion,
                                 None, output, criteria).reshape(-1, 2)
            return cv.undistortPoints(points[:, None], matrix, self.distortion,
                                      P=output, criteria=criteria).reshape(-1, 2)
        homogeneous = np.column_stack((points, np.ones(len(points))))
        rays = homogeneous @ np.linalg.inv(output).T
        return cv.projectPoints(rays, np.zeros(3), np.zeros(3), matrix, self.distortion)[0].reshape(-1, 2)


def describe_observation(symbol, method, variant, evidence, timing):
    code = normalize_ean13(symbol["raw_code"], symbol.get("type", "EAN_13"))
    reference = timing.get("frame_monotonic_ns") or timing.get("received_monotonic_ns")
    marker = evidence.lookup(code, round(reference / 1e6)) if evidence and code and reference is not None else None
    reason = None
    if code is None:
        reason = "Invalid EAN-13 checksum/type"
    elif symbol.get("quality", 2) < 2:
        reason = "Insufficient independent scanline support"
    elif symbol.get("transition"):
        reason = "Different codes on different scanlines"
    elif marker is None:
        reason = "No unique matching display journal entry"
    elif timing.get("received_monotonic_ns") is not None and marker["marker_ns"] > timing["received_monotonic_ns"]:
        reason = "Impossible: marker generated after camera receipt"
    payload_ms = unwrap_monotonic_ms(int(code[:12]), round(reference / 1e6)) if code and reference is not None else None
    marker_ns = marker["marker_ns"] if marker else None
    display = evidence.marker_timing(marker["index"]) if marker else None
    offset = ((timing["frame_monotonic_ns"] - marker_ns) / 1e6
              if marker_ns is not None and timing.get("frame_monotonic_ns") is not None else None)
    return {**symbol, "method": method, "variant": variant, "code": code,
            "payload_ms": payload_ms, "marker_ns": marker_ns,
            "display_index": marker["index"] if marker else None,
            "quadrant": QUADRANTS[marker["corner"]] if marker else "Unknown",
            "corner": marker["corner"] if marker else None,
            "offset_ms": offset, "valid": reason is None,
            "status": reason or ("Journal matched; timing " + display["status"]),
            "display_timing": display}


def choose_prediction(observations, strict, timing, evidence):
    """Prefer outline evidence; expose the newest-code fallback as provisional."""
    valid = [o for o in observations if o["valid"] and o["offset_ms"] is not None]
    base = {"offset_ms": None, "marker_ns": None, "source_ids": [],
            "status": "Unavailable", "basis": "No usable timestamp", "eligible": False,
            "strict": False, "warnings": [], "display_index": None, "quadrant": "Unknown"}
    if not valid:
        return base
    indices = sorted({o["display_index"] for o in valid})
    # Do not discard conflicting optical evidence just because one registration
    # happens to claim a clean outline or a convenient offset.
    if indices[-1] - indices[0] > 3 or len({o["display_index"] for o in valid}) != len({o["corner"] for o in valid}):
        base.update(basis="Conflicting marker generations; no single prediction",
                    warnings=["Wide temporal overlap or false decoding"])
        return base
    reliable = [s for s in strict if s.get("screen_ns") is not None]
    if reliable and len({s["display_index"] for s in reliable}) == 1:
        selected = min(reliable, key=lambda s: s["selection"] != "direct")
        index = selected["display_index"]
        marker_ns = (evidence.frames[index]["marker_ns"] if selected["selection"] == "direct"
                     else selected["screen_ns"])
        if any(not 0 <= index-i < evidence.visible_frames for i in indices):
            reliable = []
        else:
            ids = [o["id"] for o in valid if o["method"] == "Outline scanlines"
                   and o["variant"] == selected["variant"] and o["code"] == selected.get("source_ean13")]
            base.update(offset_ms=(timing["frame_monotonic_ns"]-marker_ns)/1e6,
                        marker_ns=marker_ns, source_ids=ids, display_index=index,
                        quadrant=QUADRANTS[evidence.frames[index]["corner"]],
                        status="Outline supported", basis=selected["selection"].replace("_", " "),
                        strict=True, eligible=True,
                        warnings=["Software display reference; physical exposure remains unverified"])
            return base
    latest = [o for o in valid if o["display_index"] == indices[-1]]
    newest = latest[0]
    clean = newest["display_timing"]["status"] == "clean"
    overlap = indices[-1]-indices[0] >= evidence.visible_frames
    warnings = ["Newest decoded code may not be the actual newest displayed marker"]
    if overlap:
        warnings.append("Decoded generations cannot belong to one logged display state")
    if not clean:
        warnings.extend(newest["display_timing"]["issue_codes"])
    if len({s["display_index"] for s in reliable}) > 1:
        warnings.append("Outline methods disagree on the newest marker")
    methods = sorted({o["method"] for o in latest})
    base.update(offset_ms=newest["offset_ms"], marker_ns=newest["marker_ns"],
                source_ids=[o["id"] for o in latest], display_index=newest["display_index"],
                quadrant=newest["quadrant"], status="Provisional", eligible=clean,
                basis="Newest journal-matched code; " + " + ".join(methods), warnings=warnings)
    return base


class CalibrationRecording:
    """One worker owns each instance; only compact decoded results are cached."""

    def __init__(self, folder, intrinsics=None):
        self.folder = Path(folder).expanduser().resolve()
        if not self.folder.is_dir():
            raise ValueError("Select an existing calibration recording folder")
        self.raw = load_manifest(self.folder)
        if not self.raw:
            raise ValueError("The camera timestamp journal is empty")
        epochs = load_epochs(self.folder)
        self.rows = [normalize_timing_row(row, epochs) for row in self.raw]
        names = [row["camera_frame"] for row in self.rows]
        if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
            raise ValueError("The journal contains missing or duplicate image names")
        for name in names:
            path = (self.folder / name).resolve()
            if not path.is_relative_to(self.folder) or not path.is_file():
                raise ValueError("Missing image or image outside recording folder: " + name)
        self.evidence = DisplayEvidence.load(self.folder)
        session_path = self.folder / "camera_timing_session.json"
        self.session = json.loads(session_path.read_text()) if session_path.is_file() else {}
        self.undistorter = Undistorter(intrinsics) if intrinsics else None
        self.readers, self.availability = {}, {}
        for name, factory in (("OpenCV", OpenCVReader), ("ZBar", ZBarReader)):
            try:
                self.readers[name] = factory()
            except (RuntimeError, OSError, AttributeError) as error:
                self.availability[name] = str(error)
        self.cache = OrderedDict()

    def close(self):
        for reader in self.readers.values():
            reader.close()

    def image(self, index, variant="Original"):
        frame = cv.imread(str(self.folder / self.rows[index]["camera_frame"]))
        if frame is None:
            raise ValueError("Could not read " + self.rows[index]["camera_frame"])
        if variant == "Undistorted":
            if not self.undistorter:
                raise ValueError("Load camera intrinsics to view an undistorted image")
            frame = self.undistorter.image(frame)
        return frame

    def inspect(self, index, compare_undistorted=False):
        key = index, bool(compare_undistorted)
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        frame, timing = self.image(index), self.rows[index]
        variants = [("Original", frame)]
        if compare_undistorted:
            if not self.undistorter:
                raise ValueError("Choose an intrinsic coefficients JSON before enabling undistorted decoding")
            variants.append(("Undistorted", self.undistorter.image(frame)))
        observations, strict, status = [], [], []
        for variant, pixels in variants:
            for method, reader in self.readers.items():
                try:
                    symbols = reader.decode(pixels)
                    observations.extend(describe_observation(s, method, variant, self.evidence, timing) for s in symbols)
                    status.append(f"{method} / {variant}: {len(symbols)} code(s)")
                except (cv.error, RuntimeError) as error:
                    status.append(f"{method} / {variant}: {error}")
            if self.evidence and timing.get("frame_monotonic_ns") is not None:
                selected = MarkerAnalyzer(self.evidence).analyze(pixels, round(timing["frame_monotonic_ns"]/1e6))
                selected["variant"] = variant
                # Causality applies to inferred outline times as well as payloads.
                received = timing.get("received_monotonic_ns")
                if received is not None and selected.get("screen_ns") is not None and selected["screen_ns"] > received:
                    selected.update(screen_ns=None, reason="inferred_marker_after_receipt")
                strict.append(selected)
                for symbol in selected["observations"]:
                    s = {**symbol, "raw_code": symbol["code"], "type": "EAN_13",
                         "quality": symbol.get("valid_scanlines", 0)}
                    observations.append(describe_observation(s, "Outline scanlines", variant, self.evidence, timing))
                status.append(f"Outline scanlines / {variant}: {selected.get('reason') or selected['selection']}")
        for number, observation in enumerate(observations):
            observation["id"] = str(number)
        result = {"index": index, "filename": timing["camera_frame"], "timing": timing,
                  "raw_timing": self.raw[index], "size": (frame.shape[1], frame.shape[0]),
                  "observations": observations, "methods": status + list(self.availability.values()),
                  "prediction": choose_prediction(observations, strict, timing, self.evidence)}
        self.cache[key] = result
        if len(self.cache) > 96:
            self.cache.popitem(last=False)
        return result


def summarize_predictions(results):
    epochs = sorted({r["timing"]["stream_epoch"] for r in results}, key=str)
    if len(epochs) > 1:
        by_epoch = {str(epoch): summarize_predictions([r for r in results if r["timing"]["stream_epoch"] == epoch])
                    for epoch in epochs}
        excluded = Counter()
        for report in by_epoch.values():
            excluded.update(report["excluded"])
        return {"frames": len(results), "epochs": epochs, "outline": None, "provisional": None,
                "excluded": dict(excluded), "multiple_epochs": True, "by_epoch": by_epoch}
    groups = {"outline": [], "provisional": []}
    reasons = Counter()
    for result in results:
        prediction = result["prediction"]
        if prediction["eligible"]:
            groups["outline" if prediction["strict"] else "provisional"].append(prediction["offset_ms"])
        else:
            reasons[prediction["status"]] += 1
    # Do not mix stream epochs, or optical heuristics, into one correction.
    return {"frames": len(results), "epochs": epochs,
            "outline": summarize(groups["outline"]), "provisional": summarize(groups["provisional"]),
            "excluded": dict(reasons), "multiple_epochs": len(epochs) > 1}
