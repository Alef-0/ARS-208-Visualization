from datetime import datetime, timedelta
import json
from pathlib import Path
import re
from typing import Iterable

from CONNECTION.connection_packages import RadarObject, RadarPoint
from CAPTURE.point_cloud_recorder import (
    CAMERA_DELAY_SECONDS,
    RECORDING_METADATA_NAME,
    TIMESTAMPS_METADATA_NAME,
    save_point_cloud,
)

# Older snapshot folders may contain this file. It is ignored and never created.
_LEGACY_GROUP_METADATA_NAME = "group.json"
_EXPECTED_JSON_FILES = {
    _LEGACY_GROUP_METADATA_NAME,
    RECORDING_METADATA_NAME,
    TIMESTAMPS_METADATA_NAME,
}
_INDEX_PATTERN = re.compile(r"^(?:frame|camera)_(\d+)\.(?:pcd|jpg)$", re.IGNORECASE)


def _replace_json(path: Path, value: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_json(path: Path, expected_type: type):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Could not read {path.name}: {error}") from error
    if not isinstance(value, expected_type):
        raise ValueError(f"Invalid {path.name} format")
    return value


class ManualSnapshotWriter:
    """Append a radar/image pair using the normal recording folder structure."""

    def __init__(self, folder: str | Path):
        self.folder = Path(folder).expanduser()
        self.metadata_path = self.folder / RECORDING_METADATA_NAME
        self.timestamps_path = self.folder / TIMESTAMPS_METADATA_NAME

    def _load_or_initialize(self) -> tuple[list[dict], dict[str, str]]:
        if not self.folder.is_dir():
            raise ValueError("The snapshot destination must be an existing folder")

        json_names = {path.name for path in self.folder.glob("*.json")}
        unexpected = json_names - _EXPECTED_JSON_FILES
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise ValueError("The selected folder contains unrelated JSON metadata: " + names)

        if not self.metadata_path.exists() and not self.timestamps_path.exists():
            records: list[dict] = []
            timestamps: dict[str, str] = {}
            _replace_json(self.metadata_path, records)
            _replace_json(self.timestamps_path, timestamps)
            return records, timestamps

        if not self.metadata_path.is_file() or not self.timestamps_path.is_file():
            raise ValueError(
                f"The folder must contain both {RECORDING_METADATA_NAME} and "
                f"{TIMESTAMPS_METADATA_NAME}, or contain neither"
            )

        return (
            _load_json(self.metadata_path, list),
            _load_json(self.timestamps_path, dict),
        )

    def _next_index(self, records: list[dict]) -> int:
        indexes = []
        for path in self.folder.iterdir():
            match = _INDEX_PATTERN.match(path.name)
            if match:
                indexes.append(int(match.group(1)))
        for record in records:
            for key in ("point_cloud", "camera_frame"):
                match = _INDEX_PATTERN.match(str(record.get(key, "")))
                if match:
                    indexes.append(int(match.group(1)))
        return max(indexes, default=0) + 1

    def save(
        self,
        points: Iterable[RadarPoint | RadarObject],
        radar_recorded_at: datetime,
        frame_type: str,
        image_bytes: bytes,
        camera_recorded_at: datetime,
    ) -> dict:
        records, timestamps = self._load_or_initialize()
        index = self._next_index(records)
        point_cloud_name = f"frame_{index:06d}.pcd"
        camera_name = f"camera_{index:06d}.jpg"
        point_cloud_path = self.folder / point_cloud_name
        camera_path = self.folder / camera_name
        target_radar_time = camera_recorded_at - timedelta(seconds=CAMERA_DELAY_SECONDS)
        synchronization_error_ms = (
            radar_recorded_at - target_radar_time
        ).total_seconds() * 1000.0

        created = []
        try:
            save_point_cloud(point_cloud_path, tuple(points), frame_type)
            created.append(point_cloud_path)
            camera_path.write_bytes(image_bytes)
            created.append(camera_path)

            radar_timestamp = radar_recorded_at.isoformat(timespec="microseconds")
            camera_timestamp = camera_recorded_at.isoformat(timespec="microseconds")
            timestamps[point_cloud_name] = radar_timestamp
            records.append({
                "point_cloud": point_cloud_name,
                "recorded_at": radar_timestamp,
                "frame_type": frame_type,
                "camera_frame": camera_name,
                "camera_recorded_at": camera_timestamp,
                "camera_delay_ms": int(CAMERA_DELAY_SECONDS * 1000),
                "synchronization_error_ms": round(synchronization_error_ms, 3),
            })
            _replace_json(self.timestamps_path, timestamps)
            _replace_json(self.metadata_path, records)
        except Exception:
            for path in created:
                try:
                    path.unlink()
                except OSError:
                    pass
            raise

        return {
            "folder": str(self.folder.resolve()),
            "point_cloud": point_cloud_name,
            "camera_frame": camera_name,
            "synchronization_error_ms": round(synchronization_error_ms, 3),
        }
