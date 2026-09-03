"""Offline calibration inspection and coordinate-aware decoder comparisons."""

from collections import Counter, OrderedDict
import json
from pathlib import Path

import cv2 as cv
import numpy as np

from calibration.decoding.opencv import RegionDecoder
from calibration.decoding.markers import DisplayEvidence, MarkerAnalyzer, ManualPanelAnalyzer
from calibration.decoding.regions import ManualRegions
from calibration.decoding.geometry import Undistorter
from calibration.analysis.recording import (
    load_epochs, load_manifest, normalize_ean13, normalize_timing_row,
    summarize, unwrap_monotonic_ms,
)

QUADRANTS = ("Top-left", "Top-right", "Bottom-right", "Bottom-left")


def describe_observation(symbol, method, variant, evidence, timing):
    code = normalize_ean13(symbol["raw_code"], symbol.get("type", "EAN_13"))
    reference = timing.get("frame_monotonic_ns") or timing.get("received_monotonic_ns")
    marker = evidence.lookup(code, round(reference / 1e6)) if evidence and code and reference is not None else None
    reason = None
    if code is None:
        reason = "Invalid EAN-13 checksum/type"
    elif symbol.get("quality", 2) < 2:
        reason = "Insufficient independent band support"
    elif symbol.get("transition"):
        reason = "Different codes in the same panel"
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
    """Prefer separate indicator evidence; keep newest-code readings provisional."""
    valid = [o for o in observations if o["valid"] and o["offset_ms"] is not None]
    base = {"offset_ms": None, "marker_ns": None, "source_ids": [],
            "status": "Unavailable", "basis": "No usable timestamp", "eligible": False,
            "strict": False, "warnings": [], "display_index": None, "quadrant": "Unknown"}
    if any(o.get("transition") and o.get("display_index") is not None for o in observations):
        base.update(basis="Different generations decoded in one panel",
                    warnings=["Conflicting optical evidence across bands or image variants"])
        return base
    contradictions = [s for s in strict if
                      len(s.get("outlined_corners", [])) > 1 or s.get("reason") in {
                          "expected_blank_quadrant_has_content", "mixed_display_generations",
                          "differing_valid_band_values"}]
    if contradictions:
        base.update(basis="Current-marker evidence is ambiguous",
                    warnings=sorted({s.get("reason") or "Multiple current indicators" for s in contradictions}))
        return base
    if not valid:
        return base
    indices = sorted({o["display_index"] for o in valid})
    # Do not discard conflicting optical evidence just because one registration
    # happens to claim a clean current indicator or a convenient offset.
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
            ids = [o["id"] for o in valid if o["method"] == "OpenCV"
                   and o["variant"] == selected["variant"] and o["code"] == selected.get("source_ean13")]
            base.update(offset_ms=(timing["frame_monotonic_ns"]-marker_ns)/1e6,
                        marker_ns=marker_ns, source_ids=ids, display_index=index,
                        quadrant=QUADRANTS[evidence.frames[index]["corner"]],
                        status="Current indicator supported", basis=selected["selection"].replace("_", " "),
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
        warnings.append("Image variants disagree on the current marker")
    methods = sorted({o["method"] for o in latest})
    base.update(offset_ms=newest["offset_ms"], marker_ns=newest["marker_ns"],
                source_ids=[o["id"] for o in latest], display_index=newest["display_index"],
                quadrant=newest["quadrant"], status="Provisional", eligible=clean and not overlap,
                basis="Newest journal-matched code; " + " + ".join(methods), warnings=warnings)
    return base


class CalibrationRecording:
    """One worker owns each instance; only compact decoded results are cached."""

    def __init__(self, folder, intrinsics=None, alpha=0.0, contrast=True, binary=False):
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
        self.manual_regions = ManualRegions.load(self.folder, intrinsics)
        if self.manual_regions and not intrinsics:
            intrinsics = self.manual_regions.intrinsics_path
            alpha = self.manual_regions.alpha
        self.undistorter = Undistorter(intrinsics, alpha=alpha) if intrinsics else None
        self.manual_analyzer = None
        if self.manual_regions:
            if not self.evidence:
                raise ValueError("Manual panels require the display timing journal")
            quads = self.manual_regions.for_undistorter(self.undistorter, self.manual_regions.size)
            self.manual_analyzer = ManualPanelAnalyzer(self.evidence, quads, contrast, binary,
                valid_pixels=self.undistorter.valid_pixels(self.manual_regions.size))
        self.decoders = {variant: RegionDecoder(self.evidence, contrast, binary)
                         for variant in ("Original", "Undistorted")} if not self.manual_analyzer else {}
        self.analyzers = {variant: MarkerAnalyzer(self.evidence)
                          for variant in self.decoders} if self.evidence else {}
        self.cache = OrderedDict()

    def close(self):
        self.cache.clear()

    def reset_decoding(self):
        self.cache.clear()
        for decoder in self.decoders.values():
            decoder.reset()
        for analyzer in self.analyzers.values():
            analyzer.transform = None

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
        if self.manual_analyzer:
            result = self.inspect_manual(index, frame, timing)
            self.cache[key] = result
            if len(self.cache) > 96:
                self.cache.popitem(last=False)
            return result
        variants = [("Original", frame)]
        if compare_undistorted:
            if not self.undistorter:
                raise ValueError("Choose an intrinsic coefficients JSON before enabling undistorted decoding")
            variants.append(("Undistorted", self.undistorter.image(frame)))
        observations, strict, status = [], [], []
        for variant, pixels in variants:
            reference = timing.get("frame_monotonic_ns") or timing.get("received_monotonic_ns")
            symbols = self.decoders[variant].decode(pixels, round(reference/1e6) if reference else 0,
                                                    timing.get("received_monotonic_ns"), index)
            observations.extend(describe_observation(s, "OpenCV", variant, self.evidence, timing) for s in symbols)
            status.append(f"OpenCV / {variant}: {len(symbols)} readings; "
                          f"{len(self.decoders[variant].regions())} learned regions")
            # Old borders blend with adjacent bars in camera artifacts. Keep
            # their timestamps provisional; only the separate new underline
            # can support current-marker selection in this viewer.
            if (self.evidence and self.evidence.indicator_style == "underline"
                    and timing.get("frame_monotonic_ns") is not None):
                selected = self.analyzers[variant].analyze(pixels, round(reference/1e6))
                selected["variant"] = variant
                # Causality applies to inferred current times as well as payloads.
                received = timing.get("received_monotonic_ns")
                if received is not None and selected.get("screen_ns") is not None and selected["screen_ns"] > received:
                    selected.update(screen_ns=None, reason="inferred_marker_after_receipt")
                strict.append(selected)
                for symbol in selected["observations"]:
                    s = {**symbol, "raw_code": symbol["code"], "type": "EAN_13",
                         "quality": symbol.get("valid_scanlines", 0)}
                    observations.append(describe_observation(s, "OpenCV", variant, self.evidence, timing))
                status.append(f"Current underline / {variant}: {selected.get('reason') or selected['selection']}")
        for number, observation in enumerate(observations):
            observation["id"] = str(number)
        result = {"index": index, "filename": timing["camera_frame"], "timing": timing,
                  "raw_timing": self.raw[index], "size": (frame.shape[1], frame.shape[0]),
                  "observations": observations, "methods": status,
                  "alpha": self.undistorter.alpha if self.undistorter else None,
                  "prediction": choose_prediction(observations, strict, timing, self.evidence)}
        self.cache[key] = result
        if len(self.cache) > 96:
            self.cache.popitem(last=False)
        return result

    def inspect_manual(self, index, frame, timing):
        if (frame.shape[1], frame.shape[0]) != self.manual_regions.size:
            raise ValueError("Camera frame size differs from the manual selection")
        reference = timing.get("frame_monotonic_ns") or timing.get("received_monotonic_ns")
        if reference is None:
            raise ValueError("Cannot match barcodes without a monotonic reference")
        selected = self.manual_analyzer.analyze(self.undistorter.image(frame), round(reference/1e6))
        selected["variant"] = "Undistorted"
        received = timing.get("received_monotonic_ns")
        if received is not None and selected.get("screen_ns") is not None and selected["screen_ns"] > received:
            selected.update(selection="ambiguous", screen_ns=None, reason="inferred_marker_after_receipt")
        observations = []
        for symbol in selected["observations"]:
            converted = {**symbol, "raw_code": symbol["code"], "type": "EAN_13",
                         "quality": symbol.get("valid_scanlines", 0), "location_source": "manual_region"}
            observations.append(describe_observation(converted, "OpenCV", "Undistorted", self.evidence, timing))
        for n, observation in enumerate(observations):
            observation["id"] = str(n)
        return {"index": index, "filename": timing["camera_frame"], "timing": timing,
                "raw_timing": self.raw[index], "size": (frame.shape[1], frame.shape[0]),
                "observations": observations, "alpha": self.undistorter.alpha,
                "manual_regions": str(self.manual_regions.path), "selection": selected,
                "methods": ["OpenCV / Undistorted: four fixed manual panels",
                            f"Current underline: {selected.get('reason') or selected['selection']}"],
                "prediction": choose_prediction(observations, [selected], timing, self.evidence)}


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
