#!/usr/bin/env python3
"""Measure a calibration clock against host-anchored camera PTS and NTP."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable

import cv2 as cv

from CALIBRATION.marker_analysis import DisplayEvidence, MarkerAnalyzer


EAN_PAYLOAD_DIGITS = 12
EAN_MODULUS_MS = 10**EAN_PAYLOAD_DIGITS
DEFAULT_JSON_NAME = "calibration_offset_analysis.json"
DEFAULT_CSV_NAME = "calibration_offset_frames.csv"
MAX_BARCODE_DISTANCE_MS = 60_000


def ean13_check_digit(payload: str) -> str:
    if len(payload) != EAN_PAYLOAD_DIGITS or not payload.isdigit():
        raise ValueError("EAN-13 payload must contain exactly 12 digits")
    weighted_sum = sum(int(value) for value in payload[::2])
    weighted_sum += 3 * sum(int(value) for value in payload[1::2])
    return str((-weighted_sum) % 10)


def normalize_ean13(code: str, barcode_type: str) -> str | None:
    code = code.strip()
    if barcode_type in {"UPC_A", "UPC-A"} and len(code) == 12:
        code = "0" + code
    if len(code) != 13 or not code.isdigit():
        return None
    if ean13_check_digit(code[:EAN_PAYLOAD_DIGITS]) != code[-1]:
        return None
    return code


def unwrap_monotonic_ms(encoded_ms: int, reference_ms: int) -> int:
    cycle_start = reference_ms - reference_ms % EAN_MODULUS_MS
    candidates = (
        cycle_start - EAN_MODULUS_MS + encoded_ms,
        cycle_start + encoded_ms,
        cycle_start + EAN_MODULUS_MS + encoded_ms,
    )
    return min(candidates, key=lambda value: abs(value - reference_ms))


def decode_visible_times_ms(
    image_path: Path,
    reference_ms: int,
    detector: Any,
) -> tuple[list[int], list[str]]:
    frame = cv.imread(str(image_path), cv.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"OpenCV could not read image: {image_path}")
    try:
        found, decoded_info, decoded_types, _points = (
            detector.detectAndDecodeWithType(frame)
        )
    except cv.error:
        return [], []
    if not found:
        return [], []

    decoded: dict[str, int] = {}
    for raw_code, barcode_type in zip(decoded_info, decoded_types):
        code = normalize_ean13(raw_code, barcode_type)
        if code is None:
            continue
        decoded[code] = unwrap_monotonic_ms(
            int(code[:EAN_PAYLOAD_DIGITS]),
            reference_ms,
        )
    ordered = sorted(decoded.items(), key=lambda item: item[1])
    ordered = [
        item
        for item in ordered
        if abs(item[1] - reference_ms) <= MAX_BARCODE_DISTANCE_MS
    ]
    return [value for _code, value in ordered], [code for code, _value in ordered]


def load_manifest(recording_dir: Path) -> list[dict[str, Any]]:
    jsonl_path = recording_dir / "camera_timestamps.jsonl"
    if jsonl_path.is_file():
        rows = []
        with jsonl_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(
                        f"Invalid JSON at {jsonl_path}:{line_number}: {error}"
                    ) from error
                if not isinstance(value, dict):
                    raise ValueError(
                        f"Expected an object at {jsonl_path}:{line_number}"
                    )
                rows.append(value)
        return rows

    legacy_path = recording_dir / "camera_timestamps.json"
    if legacy_path.is_file():
        value = json.loads(legacy_path.read_text(encoding="utf-8"))
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise ValueError(f"Expected a JSON array of objects in {legacy_path}")
        return value
    raise FileNotFoundError(f"Missing camera timing journal in {recording_dir}")


def load_epochs(recording_dir: Path) -> dict[int, dict[str, Any]]:
    path = recording_dir / "camera_timing_session.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    epochs = value.get("epochs", [])
    if not isinstance(epochs, list):
        raise ValueError(f"Expected an epochs array in {path}")
    return {
        int(epoch["stream_epoch"]): epoch
        for epoch in epochs
        if isinstance(epoch, dict) and isinstance(epoch.get("stream_epoch"), int)
    }


def _integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def normalize_timing_row(
    row: dict[str, Any],
    epochs: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Normalize compact and historical rows without using recorder start as PTS zero."""

    timing = row.get("timing") if isinstance(row.get("timing"), dict) else {}
    frame = row.get("frame") or row.get("camera_frame")
    stream_epoch = _integer(row.get("stream_epoch"))
    if stream_epoch is None:
        stream_epoch = _integer(timing.get("stream_epoch"))
    running_ns = _integer(row.get("running_time_ns"))
    if running_ns is None:
        running_ns = _integer(timing.get("running_time_ns"))
    pts_ns = _integer(row.get("pts_ns"))
    if pts_ns is None:
        pts_ns = _integer(timing.get("pts_ns"))
    received_monotonic_ns = _integer(row.get("received_monotonic_ns"))
    if received_monotonic_ns is None:
        received_monotonic_ns = _integer(timing.get("host_monotonic_received_ns"))
    pipeline_age_ns = _integer(timing.get("pipeline_age_ns"))

    frame_monotonic_ns = None
    epoch = epochs.get(stream_epoch) if stream_epoch is not None else None
    if epoch is not None and running_ns is not None:
        pipeline_zero = _integer(epoch.get("pipeline_zero_monotonic_ns"))
        if pipeline_zero is not None:
            frame_monotonic_ns = pipeline_zero + running_ns
    if frame_monotonic_ns is None and received_monotonic_ns is not None:
        if pipeline_age_ns is not None:
            frame_monotonic_ns = received_monotonic_ns - pipeline_age_ns

    media_unix_ns = _integer(row.get("media_unix_ns"))
    if media_unix_ns is None:
        media_unix_ns = _integer(row.get("pts_time_unix_ns"))
    if media_unix_ns is None:
        media_unix_ns = _integer(timing.get("media_time_ns"))
    if media_unix_ns is None:
        media_unix_ns = _integer(timing.get("pts_time_ns"))
    received_unix_ns = _integer(row.get("received_unix_ns"))
    if received_unix_ns is None:
        received_unix_ns = _integer(row.get("host_received_unix_ns"))
    if received_unix_ns is None:
        received_unix_ns = _integer(timing.get("host_realtime_received_ns"))

    unix_minus_monotonic_ns = None
    system_clock_error_ns = None
    if epoch is not None:
        pipeline_zero_unix_ns = _integer(epoch.get("pipeline_zero_unix_ns"))
        pipeline_zero_monotonic_ns = _integer(
            epoch.get("pipeline_zero_monotonic_ns")
        )
        if (
            pipeline_zero_unix_ns is not None
            and pipeline_zero_monotonic_ns is not None
        ):
            unix_minus_monotonic_ns = (
                pipeline_zero_unix_ns - pipeline_zero_monotonic_ns
            )
            if received_unix_ns is not None and received_monotonic_ns is not None:
                stable_received_unix_ns = (
                    received_monotonic_ns + unix_minus_monotonic_ns
                )
                system_clock_error_ns = received_unix_ns - stable_received_unix_ns
    if system_clock_error_ns is None:
        system_clock_error_ns = _integer(timing.get("system_clock_error_ns"))

    reference_ntp_ns = _integer(row.get("reference_ntp_ns"))
    if reference_ntp_ns is None:
        reference_ntp_ns = _integer(row.get("camera_ntp_unix_ns"))
    if reference_ntp_ns is None:
        reference_ntp_ns = _integer(timing.get("camera_ntp_ns"))
    return {
        "camera_frame": frame,
        "stream_epoch": stream_epoch,
        "pts_ns": pts_ns,
        "running_time_ns": running_ns,
        "frame_monotonic_ns": frame_monotonic_ns,
        "received_monotonic_ns": received_monotonic_ns,
        "received_unix_ns": received_unix_ns,
        "media_unix_ns": media_unix_ns,
        "unix_minus_monotonic_ns": unix_minus_monotonic_ns,
        "system_clock_error_ns": system_clock_error_ns,
        "reference_ntp_ns": reference_ntp_ns,
    }


def percentile(sorted_values: list[float], percentage: float) -> float:
    if not sorted_values:
        raise ValueError("Cannot calculate a percentile of an empty series")
    position = (len(sorted_values) - 1) * percentage / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    fraction = position - lower
    return sorted_values[lower] + fraction * (
        sorted_values[upper] - sorted_values[lower]
    )


def summarize(values: Iterable[float | None]) -> dict[str, float | int] | None:
    finite = sorted(
        float(value)
        for value in values
        if value is not None and math.isfinite(float(value))
    )
    if not finite:
        return None
    median = statistics.median(finite)
    absolute_deviations = sorted(abs(value - median) for value in finite)
    return {
        "count": len(finite),
        "mean": statistics.fmean(finite),
        "median": median,
        "minimum": finite[0],
        "p05": percentile(finite, 5),
        "p95": percentile(finite, 95),
        "maximum": finite[-1],
        "population_standard_deviation": statistics.pstdev(finite),
        "median_absolute_deviation": statistics.median(absolute_deviations),
    }


def analyze_recording(recording_dir: Path, *, screen_corners=None) -> dict[str, Any]:
    epochs = load_epochs(recording_dir)
    rows = [
        normalize_timing_row(row, epochs)
        for row in load_manifest(recording_dir)
    ]
    if not rows:
        raise ValueError("Timestamp journal is empty")
    for row in rows:
        if not isinstance(row["camera_frame"], str) or not row["camera_frame"]:
            raise ValueError("Every timing row must name a camera frame")
        if row["frame_monotonic_ns"] is None:
            raise ValueError(
                f"{row['camera_frame']} cannot be mapped to monotonic time; "
                "the recording has neither an epoch anchor nor receipt/pipeline-age data"
            )

    ntp_anchor = next(
        (row for row in rows if row["reference_ntp_ns"] is not None),
        None,
    )
    display_evidence = DisplayEvidence.load(recording_dir)
    marker_analyzer = MarkerAnalyzer(display_evidence, screen_corners) if display_evidence else None
    detector = None
    if marker_analyzer is None:
        if screen_corners is not None:
            raise ValueError("--screen-corners requires a display_timestamps.jsonl journal")
        detector_type = getattr(cv, "barcode_BarcodeDetector", None)
        if detector_type is None:
            raise RuntimeError("This OpenCV installation has no BarcodeDetector for legacy recordings")
        detector = detector_type()

    frame_results = []
    decode_failures = []
    excluded_frames = []
    for row in rows:
        image_path = recording_dir / row["camera_frame"]
        if not image_path.is_file():
            raise FileNotFoundError(f"Missing image named by journal: {image_path}")
        frame_monotonic_ns = int(row["frame_monotonic_ns"])
        selection = {"selection": "legacy_freshest", "added_period_ns": 0}
        if marker_analyzer is not None:
            image = cv.imread(str(image_path), cv.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"OpenCV could not read image: {image_path}")
            selection = marker_analyzer.analyze(image, round(frame_monotonic_ns / 1_000_000))
            if selection["screen_ns"] is None:
                decode_failures.append(row["camera_frame"])
                excluded_frames.append({"camera_frame": row["camera_frame"], **selection})
                continue
            observed = [item for item in selection["observations"] if item.get("display_index") is not None]
            observed.sort(key=lambda item: item["timestamp_ms"])
            visible_values_ms = [item["timestamp_ms"] for item in observed]
            visible_codes = [item["code"] for item in observed]
            screen_ns = selection["screen_ns"]
        else:
            visible_values_ms, visible_codes = decode_visible_times_ms(
                image_path, round(frame_monotonic_ns / 1_000_000), detector,
            )
            if not visible_values_ms:
                decode_failures.append(row["camera_frame"])
                excluded_frames.append({"camera_frame": row["camera_frame"],
                                        "selection": "ambiguous", "reason": "legacy_decode_failed"})
                continue
            screen_ns = max(visible_values_ms) * 1_000_000
        screen_unix_ns = (
            screen_ns + int(row["unix_minus_monotonic_ns"])
            if row["unix_minus_monotonic_ns"] is not None
            else None
        )
        ntp_monotonic_ns = None
        if ntp_anchor is not None and row["reference_ntp_ns"] is not None:
            ntp_monotonic_ns = (
                int(ntp_anchor["frame_monotonic_ns"])
                + int(row["reference_ntp_ns"])
                - int(ntp_anchor["reference_ntp_ns"])
            )
        frame_results.append({
            **row,
            "screen_selection": selection["selection"],
            "screen_source_ean13": selection.get("source_ean13", visible_codes[-1]),
            "screen_source_corner": selection.get("source_corner"),
            "display_index": selection.get("display_index"),
            "added_period_ns": selection["added_period_ns"],
            "barcode_observations": selection.get("observations", []),
            "outlined_corners": selection.get("outlined_corners", []),
            "expected_empty_corners": selection.get("expected_empty_corners", []),
            "next_display_corner": selection.get("next_corner"),
            "screen_monotonic_ms": screen_ns / 1_000_000,
            "screen_unix_ns": screen_unix_ns,
            "freshest_ean13": visible_codes[-1],
            "visible_code_count": len(visible_codes),
            "visible_span_ms": max(visible_values_ms) - min(visible_values_ms),
            "pts_screen_offset_ms": (frame_monotonic_ns - screen_ns) / 1_000_000,
            "ntp_anchored_monotonic_ns": ntp_monotonic_ns,
            "ntp_screen_offset_ms": (
                (ntp_monotonic_ns - screen_ns) / 1_000_000
                if ntp_monotonic_ns is not None
                else None
            ),
            "reference_host_offset_ms": (
                (row["reference_ntp_ns"] - row["media_unix_ns"]) / 1_000_000
                if row["reference_ntp_ns"] is not None
                and row["media_unix_ns"] is not None
                else None
            ),
            "reference_screen_absolute_offset_ms": (
                (row["reference_ntp_ns"] - screen_unix_ns) / 1_000_000
                if row["reference_ntp_ns"] is not None
                and screen_unix_ns is not None
                else None
            ),
            "receipt_media_delay_ms": (
                (row["received_unix_ns"] - row["media_unix_ns"]) / 1_000_000
                if row["received_unix_ns"] is not None
                and row["media_unix_ns"] is not None
                else None
            ),
            "system_clock_error_ms": (
                row["system_clock_error_ns"] / 1_000_000
                if row["system_clock_error_ns"] is not None
                else None
            ),
        })
    first = frame_results[0] if frame_results else None
    for result in frame_results:
        screen_delta_ns = (
            result["screen_monotonic_ms"] - first["screen_monotonic_ms"]
        ) * 1_000_000
        result["pts_relative_drift_ms"] = (
            (result["frame_monotonic_ns"] - first["frame_monotonic_ns"])
            - screen_delta_ns
        ) / 1_000_000
        result["ntp_relative_drift_ms"] = (
            (
                result["ntp_anchored_monotonic_ns"]
                - first["ntp_anchored_monotonic_ns"]
                - screen_delta_ns
            ) / 1_000_000
            if result["ntp_anchored_monotonic_ns"] is not None
            and first["ntp_anchored_monotonic_ns"] is not None
            else None
        )

    metric_fields = (
        "pts_screen_offset_ms",
        "ntp_screen_offset_ms",
        "pts_relative_drift_ms",
        "ntp_relative_drift_ms",
        "visible_span_ms",
        "reference_host_offset_ms",
        "reference_screen_absolute_offset_ms",
        "receipt_media_delay_ms",
        "system_clock_error_ms",
    )
    return {
        "recording_directory": str(recording_dir),
        "method": {
            "pts_monotonic_formula": (
                "pipeline_zero_monotonic_ns + running_time_ns; historical fallback "
                "is received_monotonic_ns - pipeline_age_ns"
            ),
            "ntp_formula": "first NTP frame monotonic time + NTP progression",
            "offset_formula": "anchored timestamp - visible screen timestamp",
            "system_clock_formula": (
                "received Unix - (received monotonic + epoch Unix/monotonic offset)"
            ),
            "screen_selection": (
                "white outlined marker, or immediate predecessor plus measured display period; "
                "unstable/ambiguous observations excluded"
                if display_evidence else "legacy maximum decoded timestamp (no outline evidence)"
            ),
            "screen_timestamp_semantics": (
                display_evidence.metadata.get("timestamp_semantics") if display_evidence else
                "legacy marker; consult recording's display implementation"
            ),
            "measured_display_period_ns": display_evidence.period_ns if display_evidence else None,
            "uncertainty": "marker quantization, submission-to-light delay, panel scanout and camera exposure remain",
        },
        "counts": {
            "journal_frames": len(rows),
            "decoded_frames": len(frame_results),
            "decode_failures": len(decode_failures),
            "direct_frames": sum(row["screen_selection"] == "direct" for row in frame_results),
            "inferred_one_period_frames": sum(row["screen_selection"] == "inferred_one_period" for row in frame_results),
        },
        "decode_failure_frames": decode_failures,
        "excluded_frames": excluded_frames,
        "metrics": {
            field: summarize(result.get(field) for result in frame_results)
            for field in metric_fields
        },
        "frames": frame_results,
        "metrics_by_selection": {
            selection: {field: summarize(row.get(field) for row in frame_results
                                        if row["screen_selection"] == selection)
                        for field in metric_fields}
            for selection in sorted({row["screen_selection"] for row in frame_results})
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = tuple(rows[0]) if rows else ("camera_frame", "screen_selection")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows({key: json.dumps(value, separators=(",", ":"))
                          if isinstance(value, (list, dict)) else value
                          for key, value in row.items()} for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Measure EAN-13 calibration images against PTS and camera NTP",
    )
    parser.add_argument("recording_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument("--screen-corners", type=float, nargs=8, metavar="PIXEL",
                        help="Optional monitor TL TR BR BL pixel x/y coordinates in camera images")
    arguments = parser.parse_args()
    recording_dir = arguments.recording_dir.expanduser().resolve()
    output_dir = arguments.output_dir.expanduser().resolve()
    if not recording_dir.is_dir():
        raise SystemExit(f"Recording folder does not exist: {recording_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = analyze_recording(recording_dir, screen_corners=arguments.screen_corners)
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        raise SystemExit(f"Analysis failed: {error}") from error
    json_path = output_dir / DEFAULT_JSON_NAME
    csv_path = output_dir / DEFAULT_CSV_NAME
    json_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_csv(csv_path, report["frames"])
    print(
        f"Decoded {report['counts']['decoded_frames']} of "
        f"{report['counts']['journal_frames']} frames."
    )
    print(f"JSON: {json_path}")
    print(f"CSV:  {csv_path}")
    if report["excluded_frames"]:
        print("Excluded-frame reasons and decoded evidence are in the JSON report. "
              "If screen registration fails, supply --screen-corners TLx TLy TRx TRy BRx BRy BLx BLy.")


if __name__ == "__main__":
    main()
