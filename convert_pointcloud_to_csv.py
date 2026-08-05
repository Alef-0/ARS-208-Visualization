#!/usr/bin/env python3
"""Convert Segcom radar recording folders from PCD to CSV.

Place this script in a directory containing one or more ``recording_*`` folders
and run it with Python. Each recording is copied to a sibling folder ending in
``-csv``. PCD frames are replaced by CSV files; JSON metadata and camera images
are copied unchanged. A ``value_dictionaries.json`` file documents coded values,
bit fields, units, and column meanings used by the CSV output.

Dependency:
    python -m pip install pypcd4 numpy
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
import shutil
import sys
from typing import Any, Iterable

try:
    import numpy as np
    from pypcd4 import PointCloud
except ImportError as error:
    raise SystemExit(
        "Missing dependency. Install it with: python -m pip install pypcd4 numpy"
    ) from error


OUTPUT_SUFFIX = "-csv"
DICTIONARY_FILENAME = "value_dictionaries.json"

DYNAMIC_PROPERTY = {
    0: "moving",
    1: "stationary",
    2: "oncoming",
    3: "stationary_candidate",
    4: "unknown",
    5: "crossing_stationary",
    6: "crossing_moving",
    7: "stopped",
}

PDH0_FALSE_ALARM_PROBABILITY = {
    0: "invalid",
    1: "probability_below_25_percent",
    2: "probability_below_50_percent",
    3: "probability_below_75_percent",
    4: "probability_below_90_percent",
    5: "probability_below_99_percent",
    6: "probability_below_99_9_percent",
    7: "probability_at_most_100_percent",
}

AMBIGUITY_STATE = {
    0: "invalid",
    1: "ambiguous",
    2: "staggered_ramp",
    3: "unambiguous",
    4: "stationary_candidates",
}

CLUSTER_INVALID_STATE = {
    0x00: "valid",
    0x01: "invalid_low_rcs",
    0x02: "invalid_near_field_artefact",
    0x03: "invalid_far_range_not_confirmed_in_near_range",
    0x04: "valid_low_rcs",
    0x05: "reserved",
    0x06: "invalid_high_mirror_probability",
    0x07: "invalid_outside_sensor_field_of_view",
    0x08: "valid_high_artefact_probability",
    0x09: "valid_suspicious_angle",
    0x0A: "valid_low_relevance",
    0x0B: "valid_high_mirror_probability",
    0x0C: "valid_outside_sensor_field_of_view",
    0x0D: "reserved",
    0x0E: "invalid_harmonics",
    0x0F: "valid_harmonics_probability",
    0x10: "valid_multi_target_probability",
    0x11: "invalid_multi_target_probability",
}

MEASUREMENT_STATE = {
    0: "deleted",
    1: "new",
    2: "measured",
    3: "predicted",
    4: "deleted_for",
    5: "new_from_merge",
    6: "reserved",
    7: "invalid",
}

PROBABILITY_OF_EXISTENCE = {
    0: "invalid",
    1: "probability_below_25_percent",
    2: "probability_below_50_percent",
    3: "probability_below_75_percent",
    4: "probability_below_90_percent",
    5: "probability_below_99_percent",
    6: "probability_below_99_9_percent",
    7: "probability_at_most_100_percent",
}

OBJECT_CLASS = {
    0: "point",
    1: "car",
    2: "truck",
    3: "reserved_01",
    4: "motorcycle",
    5: "bicycle",
    6: "wide",
    7: "reserved_02",
}

COLLISION_DETECTION_REGIONS = {
    bit: f"collision_detection_region_{bit}" for bit in range(8)
}

ENUM_COLUMNS = {
    "dynamic_property": DYNAMIC_PROPERTY,
    "pdh": PDH0_FALSE_ALARM_PROBABILITY,
    "ambiguity_state": AMBIGUITY_STATE,
    "invalid_flag": CLUSTER_INVALID_STATE,
    "measurement_state": MEASUREMENT_STATE,
    "probability_of_existence": PROBABILITY_OF_EXISTENCE,
    "object_class": OBJECT_CLASS,
}

COLUMN_DOCUMENTATION = {
    "ID": {"description": "Cluster or object identifier.", "unit": None},
    "dist_long": {"description": "Longitudinal distance from the radar.", "unit": "m"},
    "dist_latitude": {"description": "Lateral distance from the radar.", "unit": "m"},
    "velocity_longitude": {"description": "Longitudinal relative velocity.", "unit": "m/s"},
    "velocity_latitude": {"description": "Lateral relative velocity.", "unit": "m/s"},
    "dynamic_property": {"description": "Encoded target motion state.", "unit": None},
    "dynamic_property_label": {"description": "Decoded dynamic_property value.", "unit": None},
    "rcs": {"description": "Radar cross section.", "unit": "dBm²"},
    "pdh": {"description": "False-alarm probability category (Pdh0).", "unit": None},
    "pdh_label": {"description": "Decoded pdh value.", "unit": None},
    "ambiguity_state": {"description": "Velocity ambiguity state.", "unit": None},
    "ambiguity_state_label": {"description": "Decoded ambiguity_state value.", "unit": None},
    "invalid_flag": {"description": "Cluster validity/quality state.", "unit": None},
    "invalid_flag_label": {"description": "Decoded invalid_flag value.", "unit": None},
    "dist_long_rms": {"description": "RMS error of longitudinal distance.", "unit": "m"},
    "velocity_longitude_rms": {"description": "RMS error of longitudinal velocity.", "unit": "m/s"},
    "dist_latitude_rms": {"description": "RMS error of lateral distance.", "unit": "m"},
    "velocity_latitude_rms": {"description": "RMS error of lateral velocity.", "unit": "m/s"},
    "acceleration_latitude_rms": {"description": "RMS error of lateral acceleration.", "unit": "m/s²"},
    "acceleration_longitude_rms": {"description": "RMS error of longitudinal acceleration.", "unit": "m/s²"},
    "orientation_rms": {"description": "RMS error of object orientation.", "unit": "degrees"},
    "measurement_state": {"description": "Object tracker measurement state.", "unit": None},
    "measurement_state_label": {"description": "Decoded measurement_state value.", "unit": None},
    "probability_of_existence": {"description": "Encoded probability-of-existence category.", "unit": None},
    "probability_of_existence_label": {"description": "Decoded probability_of_existence value.", "unit": None},
    "acceleration_longitude": {"description": "Longitudinal relative acceleration.", "unit": "m/s²"},
    "acceleration_latitude": {"description": "Lateral relative acceleration.", "unit": "m/s²"},
    "object_class": {"description": "Encoded object classification.", "unit": None},
    "object_class_label": {"description": "Decoded object_class value.", "unit": None},
    "orientation_angle": {
        "description": "Object orientation angle; positive values follow the radar protocol convention.",
        "unit": "degrees",
    },
    "length": {"description": "Estimated object length.", "unit": "m"},
    "width": {"description": "Estimated object width.", "unit": "m"},
    "collision_detection_regions": {
        "description": "Bit mask of active collision-detection regions.",
        "unit": None,
    },
    "collision_detection_regions_label": {
        "description": "Pipe-separated names of all active collision-detection-region bits.",
        "unit": None,
    },
}

VALUE_DICTIONARIES = {
    "source": {
        "protocol": "Continental ARS40X radar CAN interface",
        "notes": (
            "Labels describe coded values present in Segcom cluster/object recordings. "
            "CSV files retain the original numeric value and add a *_label column."
        ),
    },
    "dictionaries": {
        "dynamic_property": DYNAMIC_PROPERTY,
        "pdh": PDH0_FALSE_ALARM_PROBABILITY,
        "ambiguity_state": AMBIGUITY_STATE,
        "invalid_flag": CLUSTER_INVALID_STATE,
        "measurement_state": MEASUREMENT_STATE,
        "probability_of_existence": PROBABILITY_OF_EXISTENCE,
        "object_class": OBJECT_CLASS,
        "collision_detection_regions": COLLISION_DETECTION_REGIONS,
    },
    "columns": COLUMN_DOCUMENTATION,
}


def normalize_scalar(value: Any) -> Any:
    """Convert NumPy values into CSV/JSON-safe Python scalars."""
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return ""
    return value


def integer_code(value: Any) -> int | None:
    value = normalize_scalar(value)
    if value == "" or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def enum_label(column: str, value: Any) -> str:
    code = integer_code(value)
    if code is None:
        return ""
    return ENUM_COLUMNS[column].get(code, f"unknown_{code}")


def collision_region_label(value: Any) -> str:
    mask = integer_code(value)
    if mask is None:
        return ""
    if mask == 0:
        return "none"
    labels = [label for bit, label in COLLISION_DETECTION_REGIONS.items() if mask & (1 << bit)]
    return "|".join(labels) if labels else f"unknown_mask_{mask}"


def output_fields(source_fields: Iterable[str]) -> list[str]:
    fields: list[str] = []
    for field in source_fields:
        fields.append(field)
        if field in ENUM_COLUMNS:
            fields.append(f"{field}_label")
        elif field == "collision_detection_regions":
            fields.append("collision_detection_regions_label")
    return fields


def convert_pcd(source: Path, destination: Path) -> int:
    cloud = PointCloud.from_path(str(source))
    fields = list(cloud.fields)
    values = cloud.numpy(fields)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with destination.open("w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=output_fields(fields))
        writer.writeheader()
        for raw_row in values:
            row: dict[str, Any] = {}
            for field, raw_value in zip(fields, raw_row):
                value = normalize_scalar(raw_value)
                row[field] = value
                if field in ENUM_COLUMNS:
                    row[f"{field}_label"] = enum_label(field, value)
                elif field == "collision_detection_regions":
                    row["collision_detection_regions_label"] = collision_region_label(value)
            writer.writerow(row)
    return len(values)


def is_recording_folder(path: Path) -> bool:
    return path.is_dir() and any(path.glob("*.pcd"))


def discover_recordings(root: Path) -> list[Path]:
    recordings = [path for path in root.iterdir() if is_recording_folder(path)]
    return sorted(recordings, key=lambda path: path.name)


def copy_non_pcd_files(source: Path, destination: Path) -> None:
    for entry in source.iterdir():
        if entry.suffix.lower() == ".pcd":
            continue
        target = destination / entry.name
        if entry.is_dir():
            shutil.copytree(entry, target)
        else:
            shutil.copy2(entry, target)


def convert_recording(source: Path) -> tuple[Path, int, int]:
    destination = source.with_name(source.name + OUTPUT_SUFFIX)
    if destination.exists():
        raise FileExistsError(
            f"Output already exists: {destination}. Remove or rename it before converting again."
        )

    destination.mkdir(parents=True)
    try:
        copy_non_pcd_files(source, destination)
        frame_count = 0
        point_count = 0
        for pcd_path in sorted(source.glob("*.pcd")):
            csv_path = destination / f"{pcd_path.stem}.csv"
            point_count += convert_pcd(pcd_path, csv_path)
            frame_count += 1

        (destination / DICTIONARY_FILENAME).write_text(
            json.dumps(VALUE_DICTIONARIES, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return destination, frame_count, point_count
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def main() -> int:
    root = Path(__file__).resolve().parent
    recordings = discover_recordings(root)
    if not recordings:
        print(f"No recording folders containing PCD files were found in {root}")
        return 0

    failures = 0
    for recording in recordings:
        try:
            destination, frames, points = convert_recording(recording)
            print(
                f"Converted {recording.name}: {frames} frame(s), {points} point(s) -> "
                f"{destination.name}"
            )
        except Exception as error:
            failures += 1
            print(f"Failed to convert {recording.name}: {error}", file=sys.stderr)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
