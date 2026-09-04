"""Paths and compatibility helpers for Segcom recording data files."""

from __future__ import annotations

from pathlib import Path


IMAGE_DIRECTORY_NAME = "images"
POINT_CLOUD_DIRECTORY_NAME = "point_cloud"


def _relative_data_path(directory_name: str, filename: str | Path) -> str:
    """Return a generated recording reference below one data directory."""
    return (Path(directory_name) / Path(str(filename)).name).as_posix()


def image_reference(filename: str | Path) -> str:
    return _relative_data_path(IMAGE_DIRECTORY_NAME, filename)


def point_cloud_reference(filename: str | Path) -> str:
    return _relative_data_path(POINT_CLOUD_DIRECTORY_NAME, filename)


def image_path(folder: Path, filename: str | Path) -> Path:
    return folder / image_reference(filename)


def point_cloud_path(folder: Path, filename: str | Path) -> Path:
    return folder / point_cloud_reference(filename)


def resolve_recording_file(
    folder: Path,
    reference: str | Path | None,
    directory_name: str,
) -> Path | None:
    """Resolve a JSON file reference in both current and legacy layouts.

    Current metadata stores a path such as ``images/camera_000001.jpg``.
    Legacy metadata stores only ``camera_000001.jpg``. For the latter, the
    legacy root location is checked first, then the corresponding current data
    directory. This also tolerates an old recording whose files were moved
    without rewriting its JSON metadata.
    """
    if not reference:
        return None

    root = folder.expanduser().resolve()
    raw_path = Path(str(reference))
    candidates = [root / raw_path]
    if len(raw_path.parts) == 1:
        candidates.append(root / directory_name / raw_path.name)

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            return resolved
    return None

