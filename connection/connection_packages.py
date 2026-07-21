from dataclasses import dataclass

MISSING_QUALITY = 0xFFFFFFFF

KINEMATIC_RMS_VALUES = (
    0.005, 0.006, 0.008, 0.011, 0.014, 0.018, 0.023, 0.029,
    0.038, 0.049, 0.063, 0.081, 0.105, 0.135, 0.174, 0.224,
    0.288, 0.371, 0.478, 0.616, 0.794, 1.023, 1.317, 1.697,
    2.187, 2.817, 3.630, 4.676, 6.025, 7.762, 10.000, None,
)
ORIENTATION_RMS_VALUES = (
    0.005, 0.007, 0.010, 0.014, 0.020, 0.029, 0.041, 0.058,
    0.082, 0.116, 0.165, 0.234, 0.332, 0.471, 0.669, 0.949,
    1.346, 1.909, 2.709, 3.843, 5.451, 7.734, 10.971, 15.565,
    22.081, 31.325, 44.439, 63.044, 89.437, 126.881, 180.000, None,
)


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
        return None not in (
            self.dist_long,
            self.dist_latitude,
            self.velocity_longitude,
            self.velocity_latitude,
            self.dynamic_property,
            self.rcs,
        )


@dataclass(frozen=True)
class ObjectStatus:
    number_of_objects: int
    measurement_counter: int
    interface_version: int


@dataclass
class RadarObject:
    object_id: int
    dist_long: float | None = None
    dist_latitude: float | None = None
    velocity_longitude: float | None = None
    velocity_latitude: float | None = None
    dynamic_property: int | None = None
    rcs: float | None = None
    dist_long_rms: float | None = None
    velocity_longitude_rms: float | None = None
    dist_latitude_rms: float | None = None
    velocity_latitude_rms: float | None = None
    acceleration_latitude_rms: float | None = None
    acceleration_longitude_rms: float | None = None
    orientation_rms: float | None = None
    measurement_state: int | None = None
    probability_of_existence: int | None = None

    @property
    def has_general_data(self) -> bool:
        return None not in (
            self.dist_long,
            self.dist_latitude,
            self.velocity_longitude,
            self.velocity_latitude,
            self.dynamic_property,
            self.rcs,
        )


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


class Objects_messages:
    def __init__(self):
        self.status: ObjectStatus | None = None
        self.objects: dict[int, RadarObject] = {}

    def clear(self):
        self.status = None
        self.objects.clear()

    def fill_60a(self, status: ObjectStatus):
        self.status = status

    def fill_60b(self, message: tuple):
        (
            object_id,
            dist_long,
            dist_latitude,
            velocity_longitude,
            velocity_latitude,
            dynamic_property,
            rcs,
        ) = message
        obj = self.objects.setdefault(object_id, RadarObject(object_id))
        obj.dist_long = dist_long
        obj.dist_latitude = dist_latitude
        obj.velocity_longitude = velocity_longitude
        obj.velocity_latitude = velocity_latitude
        obj.dynamic_property = dynamic_property
        obj.rcs = rcs

    def fill_60c(self, message: tuple):
        (
            object_id,
            dist_long_rms,
            velocity_longitude_rms,
            dist_latitude_rms,
            velocity_latitude_rms,
            acceleration_latitude_rms,
            acceleration_longitude_rms,
            orientation_rms,
            measurement_state,
            probability_of_existence,
        ) = message
        obj = self.objects.setdefault(object_id, RadarObject(object_id))
        obj.dist_long_rms = dist_long_rms
        obj.velocity_longitude_rms = velocity_longitude_rms
        obj.dist_latitude_rms = dist_latitude_rms
        obj.velocity_latitude_rms = velocity_latitude_rms
        obj.acceleration_latitude_rms = acceleration_latitude_rms
        obj.acceleration_longitude_rms = acceleration_longitude_rms
        obj.orientation_rms = orientation_rms
        obj.measurement_state = measurement_state
        obj.probability_of_existence = probability_of_existence

    def snapshot(self) -> tuple[RadarObject, ...]:
        return tuple(
            RadarObject(**vars(obj))
            for _, obj in sorted(self.objects.items())
            if obj.has_general_data
        )


def _check_payload(payload: bytes) -> None:
    if len(payload) != 8:
        raise ValueError(f"Expected an 8-byte CAN payload, got {len(payload)}")


def create_200_radar_configuration(
    ok_distance, distance, ok_radarpower, radarpower,
    ok_output, output, ok_rcs, rcs,
    ok_qual, quality, save_nvm,
):
    payload = bytearray(8)
    payload[0] = (
        (int(bool(ok_distance)) << 0)
        | (int(bool(ok_radarpower)) << 2)
        | (int(bool(ok_output)) << 3)
        | (int(bool(ok_qual)) << 4)
        | (int(bool(save_nvm)) << 7)
    )
    payload[1] = (distance >> 2) & 0xFF
    payload[2] = (distance & 0x03) << 6
    payload[4] = ((output & 0x03) << 3) | ((radarpower & 0x07) << 5)
    payload[5] = ((quality & 0x01) << 2) | (int(bool(save_nvm)) << 7)
    payload[6] = int(bool(ok_rcs)) | ((rcs & 0x07) << 1)
    return int.from_bytes(payload, byteorder="big", signed=False)


def read_201_radar_state(package: bytes):
    _check_payload(package)
    max_distance_cfg = (package[1] << 2) | (package[2] >> 6)
    radar_power_cfg = ((package[3] & 0x03) << 1) | ((package[4] >> 7) & 0x01)
    output_type_cfg = (package[5] >> 2) & 0x03
    send_quality_cfg = (package[5] >> 4) & 0x01
    rcs_threshold = (package[7] >> 2) & 0x07
    return (
        max_distance_cfg,
        radar_power_cfg,
        output_type_cfg,
        rcs_threshold,
        send_quality_cfg,
        hex(int.from_bytes(package, byteorder="big", signed=False)),
    )


def read_60a_object_status(package: bytes) -> ObjectStatus:
    _check_payload(package)
    return ObjectStatus(
        number_of_objects=package[0],
        measurement_counter=(package[1] << 8) | package[2],
        interface_version=(package[3] >> 4) & 0x0F,
    )


def read_60b_object_general(package: bytes):
    _check_payload(package)
    object_id = package[0]
    dist_long = (package[1] << 5) | (package[2] >> 3)
    dist_latitude = ((package[2] & 0x07) << 8) | package[3]
    velocity_longitude = (package[4] << 2) | (package[5] >> 6)
    velocity_latitude = ((package[5] & 0x3F) << 3) | (package[6] >> 5)
    dynamic_property = package[6] & 0x07
    rcs = package[7]
    return (
        object_id,
        dist_long * 0.2 - 500.0,
        dist_latitude * 0.2 - 204.6,
        velocity_longitude * 0.25 - 128.0,
        velocity_latitude * 0.25 - 64.0,
        dynamic_property,
        rcs * 0.5 - 64.0,
    )


def read_60c_object_quality(package: bytes):
    _check_payload(package)
    object_id = package[0]
    dist_long_rms = package[1] >> 3
    velocity_longitude_rms = (package[2] >> 1) & 0x1F
    dist_latitude_rms = ((package[1] & 0x07) << 2) | (package[2] >> 6)
    velocity_latitude_rms = ((package[2] & 0x01) << 4) | (package[3] >> 4)
    acceleration_latitude_rms = (package[4] >> 2) & 0x1F
    acceleration_longitude_rms = ((package[3] & 0x0F) << 1) | (package[4] >> 7)
    orientation_rms = ((package[4] & 0x03) << 3) | (package[5] >> 5)
    measurement_state = (package[6] >> 2) & 0x07
    probability_of_existence = package[6] >> 5
    return (
        object_id,
        KINEMATIC_RMS_VALUES[dist_long_rms],
        KINEMATIC_RMS_VALUES[velocity_longitude_rms],
        KINEMATIC_RMS_VALUES[dist_latitude_rms],
        KINEMATIC_RMS_VALUES[velocity_latitude_rms],
        KINEMATIC_RMS_VALUES[acceleration_latitude_rms],
        KINEMATIC_RMS_VALUES[acceleration_longitude_rms],
        ORIENTATION_RMS_VALUES[orientation_rms],
        measurement_state,
        probability_of_existence,
    )


def read_701_cluster_list(package: bytes):
    _check_payload(package)
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
    _check_payload(package)
    cluster_id = package[0]
    pdh0 = package[3] & 0x07
    ambiguity_state = package[4] & 0x07
    invalid_state = (package[4] >> 3) & 0x1F
    return cluster_id, pdh0, ambiguity_state, invalid_state
