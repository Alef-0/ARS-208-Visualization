class Clusters_messages:
    def __init__(self):
        self.max_amount = 0
        self.x = {}
        self.y = {}
        self.dyn = {}
        self.pdh = {}
        self.ambg = {}
        self.inv = {}

    def clear(self):
        self.max_amount = 0
        self.x.clear()
        self.y.clear()
        self.dyn.clear()
        self.pdh.clear()
        self.ambg.clear()
        self.inv.clear()

    def fill_701(self, message: tuple):
        cluster_id, longitudinal, lateral, dynamic_property = message
        self.max_amount = max(self.max_amount, cluster_id)
        self.y[cluster_id] = longitudinal
        self.x[cluster_id] = lateral
        self.dyn[cluster_id] = dynamic_property

    def fill_702(self, message: tuple):
        cluster_id, pdh0, ambiguity_state, invalid_state = message
        self.max_amount = max(self.max_amount, cluster_id)
        self.pdh[cluster_id] = pdh0
        self.ambg[cluster_id] = ambiguity_state
        self.inv[cluster_id] = invalid_state


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


def read_701_cluster_list(package: bytes):
    _check_payload(package)
    cluster_id = package[0]
    dist_lon = (package[1] << 5) | (package[2] >> 3)
    dist_lat = ((package[2] & 0x03) << 8) | package[3]
    dynamic_property = package[6] & 0x07
    return cluster_id, dist_lon * 0.2 - 500.0, dist_lat * 0.2 - 102.3, dynamic_property


def read_702_quality_info(package: bytes):
    _check_payload(package)
    cluster_id = package[0]
    pdh0 = package[3] & 0x07
    ambiguity_state = package[4] & 0x07
    invalid_state = (package[4] >> 3) & 0x1F
    return cluster_id, pdh0, ambiguity_state, invalid_state
