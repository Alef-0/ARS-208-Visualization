from pathlib import Path
from typing import Iterator

import numpy as np

from sensors.radar.connection_packages import MISSING_QUALITY, RadarObject, RadarPoint
from processing.recording.point_cloud_recorder import (
    CLUSTER_PCD_FIELDS,
    LEGACY_OBJECT_PCD_FIELDS,
    OBJECT_PCD_FIELDS,
)

try:
    from pypcd4 import PointCloud
except ImportError:
    PointCloud = None


def _optional_float(value: float) -> float | None:
    return None if np.isnan(value) else float(value)


def _optional_int(value: float) -> int | None:
    if np.isnan(value):
        return None
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
            self._points = self._read_objects(cloud, OBJECT_PCD_FIELDS)
        elif set(LEGACY_OBJECT_PCD_FIELDS).issubset(available_fields):
            self.frame_type = "object"
            self._points = self._read_objects(cloud, LEGACY_OBJECT_PCD_FIELDS)
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
                cluster_id=int(row[0]), dist_long=float(row[1]), dist_latitude=float(row[2]),
                velocity_longitude=_optional_float(row[3]), velocity_latitude=_optional_float(row[4]),
                dynamic_property=_optional_int(row[5]), rcs=_optional_float(row[6]),
                pdh=int(row[7]), ambiguity_state=int(row[8]), invalid_flag=int(row[9]),
            )
            for row in values
        )

    @staticmethod
    def _read_objects(cloud, fields) -> tuple[RadarObject, ...]:
        rows = cloud.numpy(fields)
        objects = []
        for row in rows:
            values = dict(zip(fields, row))
            objects.append(RadarObject(
                object_id=int(values["ID"]),
                dist_long=float(values["dist_long"]),
                dist_latitude=float(values["dist_latitude"]),
                velocity_longitude=_optional_float(values["velocity_longitude"]),
                velocity_latitude=_optional_float(values["velocity_latitude"]),
                dynamic_property=_optional_int(values["dynamic_property"]),
                rcs=_optional_float(values["rcs"]),
                dist_long_rms=_optional_float(values["dist_long_rms"]),
                velocity_longitude_rms=_optional_float(values["velocity_longitude_rms"]),
                dist_latitude_rms=_optional_float(values["dist_latitude_rms"]),
                velocity_latitude_rms=_optional_float(values["velocity_latitude_rms"]),
                acceleration_latitude_rms=_optional_float(values["acceleration_latitude_rms"]),
                acceleration_longitude_rms=_optional_float(values["acceleration_longitude_rms"]),
                orientation_rms=_optional_float(values["orientation_rms"]),
                measurement_state=_optional_int(values["measurement_state"]),
                probability_of_existence=_optional_int(values["probability_of_existence"]),
                acceleration_longitude=_optional_float(values["acceleration_longitude"]) if "acceleration_longitude" in values else None,
                acceleration_latitude=_optional_float(values["acceleration_latitude"]) if "acceleration_latitude" in values else None,
                object_class=_optional_int(values["object_class"]) if "object_class" in values else None,
                orientation_angle=_optional_float(values["orientation_angle"]) if "orientation_angle" in values else None,
                length=_optional_float(values["length"]) if "length" in values else None,
                width=_optional_float(values["width"]) if "width" in values else None,
                collision_detection_regions=_optional_int(values["collision_detection_regions"]) if "collision_detection_regions" in values else None,
            ))
        return tuple(objects)
