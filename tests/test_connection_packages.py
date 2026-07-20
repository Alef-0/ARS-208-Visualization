import unittest

from connection.connection_packages_modified import (
    create_200_radar_configuration,
    read_201_radar_state,
    read_701_cluster_list,
    read_702_quality_info,
)


class RadarPackageTests(unittest.TestCase):
    def test_701_fields_cross_byte_boundaries(self):
        dist_lon = 0x123
        dist_lat = 0x2AB
        payload = bytearray(8)
        payload[0] = 7
        payload[1] = dist_lon >> 5
        payload[2] = ((dist_lon & 0x1F) << 3) | ((dist_lat >> 8) & 0x03)
        payload[3] = dist_lat & 0xFF
        payload[6] = 6
        cluster_id, longitude, latitude, dynamic = read_701_cluster_list(payload)
        self.assertEqual(cluster_id, 7)
        self.assertAlmostEqual(longitude, dist_lon * 0.2 - 500.0)
        self.assertAlmostEqual(latitude, dist_lat * 0.2 - 102.3)
        self.assertEqual(dynamic, 6)

    def test_702_quality_fields(self):
        payload = bytearray(8)
        payload[0] = 11
        payload[3] = 5
        payload[4] = (0x12 << 3) | 3
        self.assertEqual(read_702_quality_info(payload), (11, 5, 3, 0x12))

    def test_201_masks_radar_power_and_rcs(self):
        payload = bytearray(8)
        payload[3] = 0xFE
        payload[4] = 0x80
        payload[7] = 0xFC
        _, radar_power, _, rcs, _, _ = read_201_radar_state(payload)
        self.assertEqual(radar_power, 5)
        self.assertEqual(rcs, 7)

    def test_200_round_trip_layout(self):
        raw = create_200_radar_configuration(1, 511, 1, 3, 1, 2, 1, 1, 1, 1, 1)
        payload = raw.to_bytes(8, "big")
        self.assertEqual(payload[0], 0b10011101)
        self.assertEqual((payload[1] << 2) | (payload[2] >> 6), 511)
        self.assertEqual((payload[4] >> 3) & 0x03, 2)
        self.assertEqual((payload[4] >> 5) & 0x07, 3)


if __name__ == "__main__":
    unittest.main()
