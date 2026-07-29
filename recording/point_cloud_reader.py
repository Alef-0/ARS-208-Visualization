from pathlib import Path
from typing import Iterator

import numpy as np

from connection.connection_packages import MISSING_QUALITY, RadarObject, RadarPoint
from recording.point_cloud_recorder import CLUSTER_PCD_FIELDS, OBJECT_PCD_FIELDS

try:
    from pypcd4 import PointCloud
except ImportError:  # Reported when a recording is opened.
    PointCloud = None


def _optional_float(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def _optional_int(value: float) -> int | None:
    integer = int(value)
    return None if integer == MISSING_QUALITY else integer


class PointCloudReader:
    """Read one recorded PCD frame and expose typed radar points."""

    def __init__(self, path: str | Path):
        if PointCloud is None:
            raise RuntimeError(
                "pypcd4 is required to read point clouds; install it with "
                "python -m pip install pypcd4"
            )
        self.path = Path(path).expanduser()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)

        cloud = PointCloud.from_path(str(self.path))
        available_fields = set(cloud.fields)
        if set(OBJECT_PCD_FIELDS).issubset(available_fields):
            self.frame_type = "object"
            self._points = self._read_objects(cloud)
        elif set(CLUSTER_PCD_FIELDS).issubset(available_fields):
            self.frame_type = "cluster"
            self._points = self._read_clusters(cloud)
        else:
            raise ValueError(
                f"Unsupported PCD schema in {self.path.name}: {tuple(cloud.fields)}"
            )

    @property
    def points(self) -> tuple[RadarPoint | RadarObject, ...]:
        return self._points

    @property
    def clusters(self) -> tuple[RadarPoint, ...]:
        return self._points if self.frame_type == "cluster" else ()

    @property
    def objects(self) -> tuple[RadarObject, ...]:
        return self._points if self.frame_type == "object" else ()

    def __iter__(self) -> Iterator[RadarPoint | RadarObject]:
        return iter(self._points)

    @staticmethod
    def _read_clusters(cloud) -> tuple[RadarPoint, ...]:
        values = cloud.numpy(CLUSTER_PCD_FIELDS)
        return tuple(
            RadarPoint(
                cluster_id=int(row[0]),
                dist_long=float(row[1]),
                dist_latitude=float(row[2]),
                velocity_longitude=float(row[3]),
                velocity_latitude=float(row[4]),
                dynamic_property=int(row[5]),
                rcs=float(row[6]),
                pdh=int(row[7]),
                ambiguity_state=int(row[8]),
                invalid_flag=int(row[9]),
            )
            for row in values
        )

    @staticmethod
    def _read_objects(cloud) -> tuple[RadarObject, ...]:
        values = cloud.numpy(OBJECT_PCD_FIELDS)
        return tuple(
            RadarObject(
                object_id=int(row[0]),
                dist_long=float(row[1]),
                dist_latitude=float(row[2]),
                velocity_longitude=float(row[3]),
                velocity_latitude=float(row[4]),
                dynamic_property=int(row[5]),
                rcs=float(row[6]),
                dist_long_rms=_optional_float(row[7]),
                velocity_longitude_rms=_optional_float(row[8]),
                dist_latitude_rms=_optional_float(row[9]),
                velocity_latitude_rms=_optional_float(row[10]),
                acceleration_latitude_rms=_optional_float(row[11]),
                acceleration_longitude_rms=_optional_float(row[12]),
                orientation_rms=_optional_float(row[13]),
                measurement_state=_optional_int(row[14]),
                probability_of_existence=_optional_int(row[15]),
            )
            for row in values
        )
