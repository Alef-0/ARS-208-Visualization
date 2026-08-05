from dataclasses import dataclass

from connection.message_common import check_payload


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

OBJECT_CLASSES = {
    0: "POINT",
    1: "CAR",
    2: "TRUCK",
    3: "RESERVED_01",
    4: "MOTORCYCLE",
    5: "BICYCLE",
    6: "WIDE",
    7: "RESERVED_02",
}


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
    acceleration_longitude: float | None = None
    acceleration_latitude: float | None = None
    object_class: int | None = None
    orientation_angle: float | None = None
    length: float | None = None
    width: float | None = None
    collision_detection_regions: int | None = None

    @property
    def has_general_data(self) -> bool:
        return None not in (self.dist_long, self.dist_latitude)

    @property
    def object_class_name(self) -> str | None:
        if self.object_class is None:
            return None
        return OBJECT_CLASSES.get(self.object_class, f"UNKNOWN_{self.object_class}")


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

    def fill_60d(self, message: tuple):
        (
            object_id,
            acceleration_longitude,
            object_class,
            acceleration_latitude,
            orientation_angle,
            length,
            width,
        ) = message
        obj = self.objects.setdefault(object_id, RadarObject(object_id))
        obj.acceleration_longitude = acceleration_longitude
        obj.object_class = object_class
        obj.acceleration_latitude = acceleration_latitude
        obj.orientation_angle = orientation_angle
        obj.length = length
        obj.width = width

    def fill_60e(self, message: tuple):
        object_id, collision_detection_regions = message
        obj = self.objects.setdefault(object_id, RadarObject(object_id))
        obj.collision_detection_regions = collision_detection_regions

    def snapshot(self) -> tuple[RadarObject, ...]:
        return tuple(
            RadarObject(**vars(obj))
            for _, obj in sorted(self.objects.items())
            if obj.has_general_data
        )


def read_60a_object_status(package: bytes) -> ObjectStatus:
    check_payload(package)
    return ObjectStatus(
        number_of_objects=package[0],
        measurement_counter=(package[1] << 8) | package[2],
        interface_version=(package[3] >> 4) & 0x0F,
    )


def read_60b_object_general(package: bytes):
    check_payload(package)
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
    check_payload(package)
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


def read_60d_object_extended(package: bytes):
    check_payload(package)
    object_id = package[0]
    acceleration_longitude = (package[1] << 3) | (package[2] >> 5)
    object_class = package[3] & 0x07
    acceleration_latitude = ((package[2] & 0x1F) << 4) | (package[3] >> 4)
    orientation_angle = (package[4] << 2) | (package[5] >> 6)
    length = package[6]
    width = package[7]
    return (
        object_id,
        acceleration_longitude * 0.01 - 10.0,
        object_class,
        acceleration_latitude * 0.01 - 2.5,
        orientation_angle * 0.4 - 180.0,
        length * 0.2,
        width * 0.2,
    )


def read_60e_object_warning(package: bytes):
    check_payload(package)
    return package[0], package[1]
