from dataclasses import dataclass

from CONNECTION.message_common import MISSING_QUALITY, check_payload


@dataclass
class RadarPoint:
    cluster_id: int
    dist_long: float | None = None
    dist_latitude: float | None = None
    velocity_longitude: float | None = None
    velocity_latitude: float | None = None
    dynamic_property: int | None = None
    rcs: float | None = None
    pdh: int = MISSING_QUALITY
    ambiguity_state: int = MISSING_QUALITY
    invalid_flag: int = MISSING_QUALITY

    @property
    def has_general_data(self) -> bool:
        return None not in (self.dist_long, self.dist_latitude)


class Clusters_messages:
    def __init__(self):
        self.points: dict[int, RadarPoint] = {}

    def clear(self):
        self.points.clear()

    def fill_701(self, message: tuple):
        (
            cluster_id,
            dist_long,
            dist_latitude,
            velocity_longitude,
            velocity_latitude,
            dynamic_property,
            rcs,
        ) = message
        point = self.points.setdefault(cluster_id, RadarPoint(cluster_id))
        point.dist_long = dist_long
        point.dist_latitude = dist_latitude
        point.velocity_longitude = velocity_longitude
        point.velocity_latitude = velocity_latitude
        point.dynamic_property = dynamic_property
        point.rcs = rcs

    def fill_702(self, message: tuple):
        cluster_id, pdh, ambiguity_state, invalid_flag = message
        point = self.points.setdefault(cluster_id, RadarPoint(cluster_id))
        point.pdh = pdh
        point.ambiguity_state = ambiguity_state
        point.invalid_flag = invalid_flag

    def snapshot(self) -> tuple[RadarPoint, ...]:
        return tuple(
            RadarPoint(**vars(point))
            for _, point in sorted(self.points.items())
            if point.has_general_data
        )


def read_701_cluster_list(package: bytes):
    check_payload(package)
    cluster_id = package[0]
    dist_long = (package[1] << 5) | (package[2] >> 3)
    dist_latitude = ((package[2] & 0x07) << 8) | package[3]
    velocity_longitude = (package[4] << 2) | (package[5] >> 6)
    velocity_latitude = ((package[5] & 0x3F) << 3) | (package[6] >> 5)
    dynamic_property = package[6] & 0x07
    rcs = package[7]
    return (
        cluster_id,
        dist_long * 0.2 - 500.0,
        dist_latitude * 0.2 - 102.3,
        velocity_longitude * 0.25 - 128.0,
        velocity_latitude * 0.25 - 64.0,
        dynamic_property,
        rcs * 0.5 - 64.0,
    )


def read_702_quality_info(package: bytes):
    check_payload(package)
    cluster_id = package[0]
    pdh0 = package[3] & 0x07
    ambiguity_state = package[4] & 0x07
    invalid_state = (package[4] >> 3) & 0x1F
    return cluster_id, pdh0, ambiguity_state, invalid_state
