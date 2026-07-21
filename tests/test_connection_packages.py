import unittest

from connection.connection_packages import (
    Clusters_messages,
    MISSING_QUALITY,
    create_200_radar_configuration,
    read_201_radar_state,
    read_701_cluster_list,
    read_702_quality_info,
)


class RadarPackageTests(unittest.TestCase):
    def test_701_fields_cross_byte_boundaries(self):
        dist_long = 0x123
        dist_latitude = 0x5AB
        velocity_longitude = 0x2AB
        velocity_latitude = 0x155
        payload = bytearray(8)
        payload[0] = 7
        payload[1] = dist_long >> 5
        payload[2] = ((dist_long & 0x1F) << 3) | ((dist_latitude >> 8) & 0x07)
        payload[3] = dist_latitude & 0xFF
        payload[4] = velocity_longitude >> 2
        payload[5] = ((velocity_longitude & 0x03) << 6) | ((velocity_latitude >> 3) & 0x3F)
        payload[6] = ((velocity_latitude & 0x07) << 5) | 6
        payload[7] = 180

        decoded = read_701_cluster_list(payload)

        self.assertEqual(decoded[0], 7)
        self.assertAlmostEqual(decoded[1], dist_long * 0.2 - 500.0)
        self.assertAlmostEqual(decoded[2], dist_latitude * 0.2 - 102.3)
        self.assertAlmostEqual(decoded[3], velocity_longitude * 0.25 - 128.0)
        self.assertAlmostEqual(decoded[4], velocity_latitude * 0.25 - 64.0)
        self.assertEqual(decoded[5], 6)
        self.assertAlmostEqual(decoded[6], 26.0)

    def test_702_quality_fields(self):
        payload = bytearray(8)
        payload[0] = 11
        payload[3] = 5
        payload[4] = (0x12 << 3) | 3
        self.assertEqual(read_702_quality_info(payload), (11, 5, 3, 0x12))

    def test_snapshot_keeps_general_points_without_quality(self):
        messages = Clusters_messages()
        messages.fill_701((9, 10.0, -2.0, 3.0, 0.0, 4, -12.5))

        point, = messages.snapshot()

        self.assertEqual(point.cluster_id, 9)
        self.assertEqual(point.velocity_latitude, 0.0)
        self.assertEqual(point.pdh, MISSING_QUALITY)
        self.assertEqual(point.ambiguity_state, MISSING_QUALITY)
        self.assertEqual(point.invalid_flag, MISSING_QUALITY)

    def test_quality_can_arrive_before_general_data(self):
        messages = Clusters_messages()
        messages.fill_702((4, 6, 2, 11))
        messages.fill_701((4, 5.0, 1.0, -2.0, 0.5, 3, 8.0))

        point, = messages.snapshot()

        self.assertEqual((point.pdh, point.ambiguity_state, point.invalid_flag), (6, 2, 11))

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
