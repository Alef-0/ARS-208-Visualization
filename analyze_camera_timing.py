"""Estimate camera timing offset from the Unix timestamp visible in recorded images.

Run this after a calibration camera recording:

    python3 analyze_camera_timing.py "/path/to/recording"

Python packages are listed in requirements.txt. Tesseract itself is a system
program; on Ubuntu/Debian install it with ``sudo apt install tesseract-ocr``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
import re
from statistics import median
import sys
from typing import Iterable, Sequence

import cv2 as cv
import numpy as np


DEFAULT_MANIFEST_NAME = "camera_timestamps.json"
DEFAULT_OUTPUT_NAME = "camera_timing_analysis.json"
DEFAULT_ROI = (0.08, 0.32, 0.92, 0.72)
_DECIMAL_UNIX_PATTERN = re.compile(
    r"(?<!\d)(\d{10})\s*[.,:]\s*(\d{3,6})(?!\d)"
)
_COMPACT_UNIX_PATTERN = re.compile(r"(?<!\d)(\d{13,16})(?!\d)")


@dataclass(frozen=True)
class OcrAttempt:
    text: str
    confidence: float
    preprocessing: str


@dataclass(frozen=True)
class OcrReading:
    value: float
    confidence: float
    text: str
    preprocessing: str


def extract_unix_candidates(text: str) -> list[float]:
    """Extract seconds.fraction Unix values from imperfect OCR output."""
    normalized = text.upper().translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    compact = re.sub(r"\s+", "", normalized)
    values: list[float] = []

    for match in _DECIMAL_UNIX_PATTERN.finditer(compact):
        values.append(float(f"{match.group(1)}.{match.group(2)}"))
    for match in _COMPACT_UNIX_PATTERN.finditer(compact):
        digits = match.group(1)
        values.append(float(f"{digits[:10]}.{digits[10:]}"))

    return list(dict.fromkeys(values))


def choose_reading(
    attempts: Iterable[OcrAttempt],
    expected_unix: float,
    max_difference_seconds: float,
) -> OcrReading | None:
    """Choose the most confident plausible OCR result near the manifest time."""
    readings = [
        OcrReading(value, attempt.confidence, attempt.text, attempt.preprocessing)
        for attempt in attempts
        for value in extract_unix_candidates(attempt.text)
        if abs(value - expected_unix) <= max_difference_seconds
    ]
    if not readings:
        return None
    return max(
        readings,
        key=lambda reading: (
            reading.confidence,
            -abs(reading.value - expected_unix),
        ),
    )


def _parse_roi(value: str) -> tuple[float, float, float, float]:
    try:
        roi = tuple(float(part.strip()) for part in value.split(","))
    except ValueError as error:
        raise argparse.ArgumentTypeError("ROI values must be numbers") from error
    if len(roi) != 4:
        raise argparse.ArgumentTypeError("ROI must contain x1,y1,x2,y2")
    x1, y1, x2, y2 = roi
    if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
        raise argparse.ArgumentTypeError(
            "ROI coordinates must be fractions from 0 to 1 with x1 < x2 and y1 < y2"
        )
    return x1, y1, x2, y2


def _crop(image: np.ndarray, roi: Sequence[float]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = roi
    return image[
        round(y1 * height) : round(y2 * height),
        round(x1 * width) : round(x2 * width),
    ]


def _preprocess_images(image: np.ndarray) -> list[tuple[str, np.ndarray]]:
    gray = cv.cvtColor(image, cv.COLOR_BGR2GRAY)
    gray = cv.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    if gray.shape[0] < 500:
        scale = 500 / gray.shape[0]
        gray = cv.resize(gray, None, fx=scale, fy=scale, interpolation=cv.INTER_CUBIC)
    gray = cv.GaussianBlur(gray, (3, 3), 0)
    _, threshold = cv.threshold(gray, 0, 255, cv.THRESH_BINARY + cv.THRESH_OTSU)
    threshold = cv.morphologyEx(
        threshold,
        cv.MORPH_CLOSE,
        cv.getStructuringElement(cv.MORPH_RECT, (3, 3)),
    )
    return [("contrast", gray), ("threshold", threshold)]


def _ocr_attempts(image: np.ndarray) -> list[OcrAttempt]:
    try:
        import pytesseract
        from pytesseract import Output
    except ImportError as error:
        raise RuntimeError(
            "pytesseract is missing. Run: python3 -m pip install pytesseract"
        ) from error

    config = "--psm 6 -c tessedit_char_whitelist=0123456789.,:"
    attempts = []
    try:
        for preprocessing, processed in _preprocess_images(image):
            data = pytesseract.image_to_data(processed, config=config, output_type=Output.DICT)
            words = [word.strip() for word in data["text"] if word.strip()]
            confidences = [
                float(value)
                for value in data["conf"]
                if str(value).strip() and float(value) >= 0
            ]
            attempts.append(
                OcrAttempt(
                    text=" ".join(words),
                    confidence=max(confidences, default=0.0),
                    preprocessing=preprocessing,
                )
            )
    except pytesseract.TesseractNotFoundError as error:
        raise RuntimeError(
            "Tesseract OCR is not installed or is not on PATH. On Ubuntu/Debian run: "
            "sudo apt install tesseract-ocr"
        ) from error
    return attempts


def _unix_value(entry: dict, unix_key: str, iso_key: str) -> float:
    if entry.get(unix_key) is not None:
        return float(entry[unix_key])
    value = entry.get(iso_key)
    if not value:
        raise ValueError(f"Manifest entry is missing {unix_key!r} and {iso_key!r}")
    parsed = datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        raise ValueError(f"Manifest timestamp {iso_key!r} must include a timezone")
    return parsed.timestamp()


def _round_ms(value: float) -> float:
    return round(value * 1000.0, 3)


def _summary(
    results: Sequence[dict],
    current_adjustment_ms: float | None,
    frames_in_manifest: int | None = None,
) -> dict:
    successful = [result for result in results if result["status"] == "ok"]
    captured_offsets = [result["offset_from_captured_ms"] for result in successful]
    adjusted_offsets = [result["offset_from_adjusted_ms"] for result in successful]
    estimated_latencies = [result["estimated_camera_latency_ms"] for result in successful]
    return {
        "frames_in_manifest": len(results) if frames_in_manifest is None else frames_in_manifest,
        "frames_analyzed": len(results),
        "frames_read_successfully": len(successful),
        "frames_without_reading": len(results) - len(successful),
        "current_latency_adjustment_ms": current_adjustment_ms,
        "median_offset_from_captured_ms": (
            round(median(captured_offsets), 3) if captured_offsets else None
        ),
        "median_offset_from_adjusted_ms": (
            round(median(adjusted_offsets), 3) if adjusted_offsets else None
        ),
        "estimated_camera_latency_ms": (
            round(median(estimated_latencies), 3) if estimated_latencies else None
        ),
    }


def analyze_recording(
    recording_dir: Path,
    manifest_path: Path,
    roi: Sequence[float],
    max_difference_seconds: float,
    every: int,
    debug_dir: Path | None,
) -> dict:
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError("Camera timestamp manifest must contain a JSON list")
    if debug_dir is not None:
        debug_dir.mkdir(parents=True, exist_ok=True)

    results = []
    adjustments = []
    for index, entry in enumerate(entries):
        if index % every:
            continue
        frame_name = str(entry.get("camera_frame", ""))
        captured_unix = _unix_value(entry, "captured_at_unix", "captured_at")
        adjusted_unix = _unix_value(entry, "adjusted_at_unix", "adjusted_at")
        if entry.get("latency_adjustment_ms") is not None:
            adjustments.append(float(entry["latency_adjustment_ms"]))
        result = {
            "camera_frame": frame_name,
            "captured_at_unix": captured_unix,
            "adjusted_at_unix": adjusted_unix,
            "displayed_unix": None,
            "offset_from_captured_ms": None,
            "offset_from_adjusted_ms": None,
            "estimated_camera_latency_ms": None,
            "ocr_confidence": None,
            "ocr_text": None,
            "preprocessing": None,
            "status": "no_reading",
        }

        image = cv.imread(str(recording_dir / frame_name))
        if image is None:
            result["status"] = "image_missing_or_unreadable"
            results.append(result)
            continue
        cropped = _crop(image, roi)
        attempts = _ocr_attempts(cropped)
        reading = choose_reading(attempts, captured_unix, max_difference_seconds)
        result["ocr_text"] = " | ".join(
            f"{attempt.preprocessing}: {attempt.text}" for attempt in attempts
        )
        if reading is not None:
            result.update({
                "displayed_unix": reading.value,
                "offset_from_captured_ms": _round_ms(reading.value - captured_unix),
                "offset_from_adjusted_ms": _round_ms(reading.value - adjusted_unix),
                "estimated_camera_latency_ms": _round_ms(captured_unix - reading.value),
                "ocr_confidence": round(reading.confidence, 3),
                "preprocessing": reading.preprocessing,
                "status": "ok",
            })
        if debug_dir is not None:
            cv.imwrite(str(debug_dir / frame_name), cropped)
        results.append(result)

    current_adjustment = median(adjustments) if adjustments else None
    return {
        "recording_directory": str(recording_dir),
        "manifest": str(manifest_path),
        "roi": list(roi),
        "max_difference_seconds": max_difference_seconds,
        "analyzed_every_nth_frame": every,
        "summary": _summary(results, current_adjustment, len(entries)),
        "frames": results,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Read the Unix timestamp displayed in calibration camera images and compare "
            "it with camera_timestamps.json."
        )
    )
    parser.add_argument("recording_dir", type=Path, help="Folder containing camera images")
    parser.add_argument(
        "--manifest",
        type=Path,
        help=f"Manifest path (default: <recording_dir>/{DEFAULT_MANIFEST_NAME})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help=f"Analysis path (default: <recording_dir>/{DEFAULT_OUTPUT_NAME})",
    )
    parser.add_argument(
        "--roi",
        type=_parse_roi,
        default=DEFAULT_ROI,
        metavar="X1,Y1,X2,Y2",
        help=(
            "Fractional image crop containing the large Unix number "
            f"(default: {','.join(str(value) for value in DEFAULT_ROI)})"
        ),
    )
    parser.add_argument(
        "--max-difference",
        type=float,
        default=2.0,
        help="Reject OCR timestamps farther than this many seconds from the manifest",
    )
    parser.add_argument(
        "--every",
        type=int,
        default=1,
        help="Analyze every Nth frame (default: 1)",
    )
    parser.add_argument(
        "--debug-dir",
        type=Path,
        help="Optional folder where the image crops sent to OCR are saved",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.every <= 0:
        parser.error("--every must be greater than zero")
    if args.max_difference <= 0:
        parser.error("--max-difference must be greater than zero")

    recording_dir = args.recording_dir.expanduser().resolve()
    manifest_path = (
        args.manifest.expanduser().resolve()
        if args.manifest
        else recording_dir / DEFAULT_MANIFEST_NAME
    )
    output_path = (
        args.output.expanduser().resolve()
        if args.output
        else recording_dir / DEFAULT_OUTPUT_NAME
    )
    try:
        report = analyze_recording(
            recording_dir,
            manifest_path,
            args.roi,
            args.max_difference,
            args.every,
            args.debug_dir.expanduser().resolve() if args.debug_dir else None,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    summary = report["summary"]
    print(f"Analysis written to: {output_path}")
    print(
        f"Read {summary['frames_read_successfully']} of "
        f"{summary['frames_analyzed']} analyzed frames."
    )
    if summary["estimated_camera_latency_ms"] is not None:
        print(
            "Estimated camera latency: "
            f"{summary['estimated_camera_latency_ms']:.3f} ms"
        )
        print(
            "Median residual after current adjustment: "
            f"{summary['median_offset_from_adjusted_ms']:+.3f} ms"
        )
    else:
        print("No plausible Unix timestamps were read. Try --debug-dir and adjust --roi.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
