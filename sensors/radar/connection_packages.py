from sensors.radar.cluster_messages import (
    Clusters_messages,
    RadarPoint,
    read_701_cluster_list,
    read_702_quality_info,
)
from sensors.radar.message_common import MISSING_QUALITY, check_payload
from sensors.radar.object_messages import (
    KINEMATIC_RMS_VALUES,
    OBJECT_CLASSES,
    ORIENTATION_RMS_VALUES,
    ObjectStatus,
    Objects_messages,
    RadarObject,
    read_60a_object_status,
    read_60b_object_general,
    read_60c_object_quality,
    read_60d_object_extended,
    read_60e_object_warning,
)


# Compatibility alias for older imports from this module.
_check_payload = check_payload


def create_200_radar_configuration(
    ok_distance, distance, ok_radarpower, radarpower,
    ok_output, output, ok_rcs, rcs,
    ok_qual, quality, save_nvm,
    ok_ext=False, ext_info=0, ok_relay=False, ctrl_relay=0,
):
    payload = bytearray(8)
    payload[0] = (
        (int(bool(ok_distance)) << 0)
        | (int(bool(ok_radarpower)) << 2)
        | (int(bool(ok_output)) << 3)
        | (int(bool(ok_qual)) << 4)
        | (int(bool(ok_ext)) << 5)
        | (int(bool(save_nvm)) << 7)
    )
    payload[1] = (distance >> 2) & 0xFF
    payload[2] = (distance & 0x03) << 6
    payload[4] = ((output & 0x03) << 3) | ((radarpower & 0x07) << 5)
    payload[5] = (
        (int(bool(ok_relay)) << 0)
        | (int(bool(ctrl_relay)) << 1)
        | ((quality & 0x01) << 2)
        | ((ext_info & 0x01) << 3)
        | (int(bool(save_nvm)) << 7)
    )
    payload[6] = int(bool(ok_rcs)) | ((rcs & 0x07) << 1)
    return int.from_bytes(payload, byteorder="big", signed=False)


def read_201_radar_state_extended(package: bytes):
    check_payload(package)
    max_distance_cfg = (package[1] << 2) | (package[2] >> 6)
    radar_power_cfg = ((package[3] & 0x03) << 1) | ((package[4] >> 7) & 0x01)
    output_type_cfg = (package[5] >> 2) & 0x03
    ctrl_relay_cfg = (package[5] >> 1) & 0x01
    send_quality_cfg = (package[5] >> 4) & 0x01
    send_ext_info_cfg = (package[5] >> 5) & 0x01
    rcs_threshold = (package[7] >> 2) & 0x07
    raw_payload = hex(int.from_bytes(package, byteorder="big", signed=False))
    return (
        max_distance_cfg,
        radar_power_cfg,
        output_type_cfg,
        rcs_threshold,
        send_quality_cfg,
        send_ext_info_cfg,
        ctrl_relay_cfg,
        raw_payload,
    )


def read_201_radar_state(package: bytes):
    (
        max_distance_cfg,
        radar_power_cfg,
        output_type_cfg,
        rcs_threshold,
        send_quality_cfg,
        _,
        _,
        raw_payload,
    ) = read_201_radar_state_extended(package)
    return (
        max_distance_cfg,
        radar_power_cfg,
        output_type_cfg,
        rcs_threshold,
        send_quality_cfg,
        raw_payload,
    )
