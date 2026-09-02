from datetime import datetime, timezone
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from sensors.camera.camera_pipeline_policy import (
    CPU_BACKEND,
    FrameTimestampResult,
    FrameTimestampPolicy,
    ORIN_BACKEND,
    RTX_BACKEND,
    available_decoder_backends,
    build_camera_pipeline,
)
from sensors.camera.camera_gstreamer import GStreamerPipeline


class FakeBuffer:
    def __init__(self, pts, reference_timestamp=None):
        self.pts = pts
        self._reference_timestamp = reference_timestamp

    def get_reference_timestamp_meta(self, _caps):
        if self._reference_timestamp is None:
            return None
        return type("ReferenceMeta", (), {"timestamp": self._reference_timestamp})()


class FakeSegment:
    @staticmethod
    def to_running_time(_format, pts):
        return pts


class FakeSample:
    def __init__(self, buffer):
        self._buffer = buffer

    def get_buffer(self):
        return self._buffer

    @staticmethod
    def get_segment():
        return FakeSegment()


class FakeClock:
    def __init__(self, value):
        self.value = value

    def get_time(self):
        return self.value


class FakePipeline:
    def __init__(self, running_time):
        self.clock = FakeClock(running_time)

    def get_clock(self):
        return self.clock

    @staticmethod
    def get_base_time():
        return 0


class FakeSink:
    def __init__(self, sample):
        self.sample = sample

    def emit(self, _signal):
        return self.sample


class CameraPipelinePolicyTests(unittest.TestCase):
    def test_desktop_prefers_rtx_and_keeps_cpu_fallback(self):
        elements = {*RTX_BACKEND.required_elements, *CPU_BACKEND.required_elements}
        backends = available_decoder_backends(
            factory_find=lambda name: object() if name in elements else None,
            jetson=False,
        )
        self.assertEqual([backend.name for backend in backends], ["rtx", "cpu"])

    def test_jetson_prefers_orin_and_keeps_cpu_fallback(self):
        elements = {*ORIN_BACKEND.required_elements, *CPU_BACKEND.required_elements}
        backends = available_decoder_backends(
            factory_find=lambda name: object() if name in elements else None,
            jetson=True,
        )
        self.assertEqual([backend.name for backend in backends], ["orin", "cpu"])

    def test_missing_requested_hardware_decoder_falls_back_to_cpu(self):
        elements = set(CPU_BACKEND.required_elements)
        backends = available_decoder_backends(
            "rtx",
            factory_find=lambda name: object() if name in elements else None,
        )
        self.assertEqual(backends, (CPU_BACKEND,))

    def test_pipeline_contains_selected_decoder_and_low_latency_sinks(self):
        description = build_camera_pipeline(
            ORIN_BACKEND,
            display_width=1280,
            display_height=720,
            latency_ms=250,
        )
        self.assertIn("nvv4l2decoder ! nvvidconv", description)
        self.assertIn("rtph264depay ! h264parse !", description)
        self.assertIn("protocols=tcp+udp", description)
        self.assertIn("buffer-mode=1", description)
        self.assertIn("do-retransmission=true", description)
        self.assertIn("latency=250", description)
        self.assertNotIn("name=depay", description)
        self.assertNotIn("name=parser", description)
        self.assertIn("width=1280,height=720", description)
        self.assertEqual(description.count("max-buffers=1 drop=true"), 1)
        self.assertIn("queue name=capture_queue", description)
        capture_sink = description.split("appsink name=capture_sink", 1)[1]
        self.assertNotIn("max-buffers", capture_sink)
        self.assertNotIn("drop=true", capture_sink)

    def test_invalid_pts_is_rejected(self):
        policy = FrameTimestampPolicy()
        result = policy.timestamp_for_sample(FakeSample(FakeBuffer(Gst.CLOCK_TIME_NONE)))
        self.assertFalse(result.valid)
        self.assertIn("invalid PTS", result.reason)

    def test_zero_ntp_reference_falls_back_to_pts(self):
        policy = FrameTimestampPolicy()
        policy.reset(FakePipeline(Gst.SECOND))
        result = policy.timestamp_for_sample(
            FakeSample(FakeBuffer(Gst.SECOND, reference_timestamp=0))
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "pts-only")
        self.assertIsNone(result.camera_ntp_ns)

    def test_valid_pts_uses_pipeline_clock_when_ntp_meta_is_absent(self):
        running_time = 10 * Gst.SECOND
        policy = FrameTimestampPolicy()
        policy.reset(FakePipeline(running_time))
        result = policy.timestamp_for_sample(
            FakeSample(FakeBuffer(running_time - 100 * Gst.MSECOND))
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "pts-only")
        self.assertLess(abs(result.captured_at.timestamp() - (time.time() - 0.1)), 0.1)

    def test_reference_timestamp_anchors_hybrid_clock(self):
        running_time = Gst.SECOND
        host_time_ns = 100 * Gst.SECOND
        policy = FrameTimestampPolicy()
        policy.reset(FakePipeline(running_time))
        with patch(
            "sensors.camera.camera_pipeline_policy.time.time_ns",
            return_value=host_time_ns,
        ):
            result = policy.timestamp_for_sample(
                FakeSample(FakeBuffer(running_time, reference_timestamp=host_time_ns))
            )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "pts-disciplined-by-ntp")
        self.assertEqual(result.reference_clock_offset_seconds, 0.0)
        self.assertEqual(result.camera_ntp_ns, host_time_ns)
        self.assertEqual(result.timing["camera_ntp_status"], "anchor")
        self.assertEqual(result.timing["filtered_ntp_correction_ns"], 0)

    def test_large_stable_camera_clock_offset_is_not_rejected(self):
        policy = FrameTimestampPolicy(max_clock_offset_seconds=1.0)
        policy.reset(FakePipeline(Gst.SECOND))
        host_time_ns = 100 * Gst.SECOND
        reference_time_ns = host_time_ns + 25 * Gst.SECOND
        with patch(
            "sensors.camera.camera_pipeline_policy.time.time_ns",
            return_value=host_time_ns,
        ):
            result = policy.timestamp_for_sample(
                FakeSample(FakeBuffer(Gst.SECOND, reference_timestamp=reference_time_ns))
            )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "pts-disciplined-by-ntp")
        self.assertAlmostEqual(result.reference_clock_offset_seconds, 25.0)
        self.assertIn("DVR RTCP/NTP clock offset", result.reference_warning)

    def test_pipeline_clock_mapping_uses_a_fixed_anchor(self):
        pipeline = FakePipeline(10 * Gst.SECOND)
        policy = FrameTimestampPolicy()
        policy.reset(pipeline)
        with patch(
            "sensors.camera.camera_pipeline_policy.time.time_ns",
            side_effect=(100 * Gst.SECOND, 500 * Gst.SECOND),
        ):
            first = policy.timestamp_for_sample(
                FakeSample(FakeBuffer(9 * Gst.SECOND))
            )
            pipeline.clock.value = 20 * Gst.SECOND
            second = policy.timestamp_for_sample(
                FakeSample(FakeBuffer(10 * Gst.SECOND))
            )
        self.assertEqual(
            second.captured_at.timestamp() - first.captured_at.timestamp(),
            1.0,
        )

    def test_small_ntp_error_is_filtered_into_pts_prediction(self):
        pipeline = FakePipeline(Gst.SECOND)
        policy = FrameTimestampPolicy()
        policy.reset(pipeline)
        with (
            patch(
                "sensors.camera.camera_pipeline_policy.time.time_ns",
                side_effect=(100 * Gst.SECOND, 101 * Gst.SECOND),
            ),
            patch(
                "sensors.camera.camera_pipeline_policy.time.monotonic_ns",
                side_effect=(10 * Gst.SECOND, 11 * Gst.SECOND),
            ),
        ):
            policy.timestamp_for_sample(FakeSample(FakeBuffer(
                Gst.SECOND,
                reference_timestamp=125 * Gst.SECOND,
            )))
            pipeline.clock.value = 2 * Gst.SECOND
            result = policy.timestamp_for_sample(FakeSample(FakeBuffer(
                2 * Gst.SECOND,
                reference_timestamp=126 * Gst.SECOND + 5 * Gst.MSECOND,
            )))

        self.assertEqual(result.timing["camera_ntp_status"], "tracking")
        self.assertEqual(result.timing["target_ntp_correction_ns"], 625_000)
        self.assertEqual(result.timing["filtered_ntp_correction_ns"], 625_000)
        self.assertEqual(result.timing["attempted_capture_time_ns"], 101_000_625_000)

    def test_large_ntp_step_requires_confirmation_and_slews(self):
        pipeline = FakePipeline(Gst.SECOND)
        policy = FrameTimestampPolicy()
        policy.reset(pipeline)
        with (
            patch(
                "sensors.camera.camera_pipeline_policy.time.time_ns",
                side_effect=tuple(value * Gst.SECOND for value in (100, 101, 102, 103)),
            ),
            patch(
                "sensors.camera.camera_pipeline_policy.time.monotonic_ns",
                side_effect=tuple(value * Gst.SECOND for value in (10, 11, 12, 13)),
            ),
        ):
            policy.timestamp_for_sample(FakeSample(FakeBuffer(
                Gst.SECOND,
                reference_timestamp=125 * Gst.SECOND,
            )))
            results = []
            for second in (2, 3, 4):
                pipeline.clock.value = second * Gst.SECOND
                results.append(policy.timestamp_for_sample(FakeSample(FakeBuffer(
                    second * Gst.SECOND,
                    reference_timestamp=(124 + second) * Gst.SECOND + 100 * Gst.MSECOND,
                ))))

        self.assertEqual(results[0].timing["camera_ntp_status"], "step-candidate-1/3")
        self.assertEqual(results[1].timing["camera_ntp_status"], "step-candidate-2/3")
        self.assertEqual(results[2].timing["camera_ntp_status"], "step-confirmed-slewing")
        self.assertEqual(results[2].timing["target_ntp_correction_ns"], 100 * Gst.MSECOND)
        self.assertEqual(results[2].timing["filtered_ntp_correction_ns"], Gst.MSECOND)

    def test_duplicate_ntp_is_marked_invalid_but_pts_prediction_continues(self):
        pipeline = FakePipeline(Gst.SECOND)
        policy = FrameTimestampPolicy()
        policy.reset(pipeline)
        with (
            patch(
                "sensors.camera.camera_pipeline_policy.time.time_ns",
                side_effect=(100 * Gst.SECOND, 101 * Gst.SECOND),
            ),
            patch(
                "sensors.camera.camera_pipeline_policy.time.monotonic_ns",
                side_effect=(10 * Gst.SECOND, 11 * Gst.SECOND),
            ),
        ):
            policy.timestamp_for_sample(FakeSample(FakeBuffer(
                Gst.SECOND,
                reference_timestamp=125 * Gst.SECOND,
            )))
            pipeline.clock.value = 2 * Gst.SECOND
            result = policy.timestamp_for_sample(FakeSample(FakeBuffer(
                2 * Gst.SECOND,
                reference_timestamp=125 * Gst.SECOND,
            )))

        self.assertFalse(result.timing["camera_ntp_valid"])
        self.assertEqual(
            result.timing["camera_ntp_status"],
            "invalid-backward-or-duplicate",
        )
        self.assertEqual(result.timing["predicted_camera_ntp_ns"], 126 * Gst.SECOND)
        self.assertEqual(result.timing["attempted_capture_time_ns"], 101 * Gst.SECOND)

    def test_duplicate_pts_is_rejected_after_a_valid_frame(self):
        running_time = 10 * Gst.SECOND
        policy = FrameTimestampPolicy()
        policy.reset(FakePipeline(running_time))
        sample = FakeSample(FakeBuffer(Gst.SECOND))
        self.assertTrue(policy.timestamp_for_sample(sample).valid)
        result = policy.timestamp_for_sample(sample)
        self.assertFalse(result.valid)
        self.assertIn("duplicated", result.reason)

    def test_capture_callback_skips_recording_when_timestamp_is_invalid(self):
        pipeline = object.__new__(GStreamerPipeline)
        pipeline.first_frame_received = True
        pipeline._sample_to_frame = lambda _sample: object()
        pipeline.timestamp_policy = type(
            "Policy",
            (),
            {
                "timestamp_for_sample": lambda _self, _sample: FrameTimestampResult(
                    None, reason="frame has invalid PTS"
                )
            },
        )()
        submitted = []
        pipeline.snapshot_recorder = type(
            "Recorder",
            (),
            {"submit": lambda _self, *args, **kwargs: submitted.append((args, kwargs))},
        )()
        rejected = []
        pipeline._reject_synchronized_frame = rejected.append
        pipeline._emit_manual_snapshot = lambda *_args: self.fail(
            "manual snapshot must not receive an invalid timestamp"
        )

        result = pipeline.on_new_capture_sample(FakeSink(object()))

        self.assertEqual(result, Gst.FlowReturn.OK)
        self.assertEqual(submitted, [])
        self.assertEqual(rejected, ["frame has invalid PTS"])

    def test_capture_callback_sends_only_valid_timestamp_to_both_savers(self):
        captured_at = datetime.now(timezone.utc)
        frame = object()
        pipeline = object.__new__(GStreamerPipeline)
        pipeline.first_frame_received = True
        pipeline._last_timestamp_diagnostic = time.monotonic()
        pipeline._sample_to_frame = lambda _sample: frame
        pipeline.timestamp_policy = type(
            "Policy",
            (),
            {
                "timestamp_for_sample": lambda _self, _sample: FrameTimestampResult(
                    captured_at,
                    source="pipeline-clock",
                    receipt_offset_seconds=0.25,
                )
            },
        )()
        submitted = []
        pipeline.snapshot_recorder = type(
            "Recorder",
            (),
            {"submit": lambda _self, *args, **kwargs: submitted.append((args, kwargs))},
        )()
        observed_ntp = []
        pipeline._observe_camera_ntp = lambda *args: observed_ntp.append(args)
        pipeline._report_pipeline_frame_gap = lambda _timing: None
        manual = []
        pipeline._emit_manual_snapshot = lambda *args: manual.append(args)

        sample = FakeSample(FakeBuffer(Gst.SECOND))
        result = pipeline.on_new_capture_sample(FakeSink(sample))

        self.assertEqual(result, Gst.FlowReturn.OK)
        self.assertEqual(submitted, [((frame,), {
            "captured_at": captured_at,
            "timing": {
                "timestamp_source": "pipeline-clock",
                "reference_clock_offset_seconds": None,
            },
        })])
        self.assertEqual(observed_ntp, [(None, Gst.SECOND, None)])
        self.assertEqual(manual, [(frame, captured_at)])

    def test_ntp_jump_warning_compares_ntp_progress_with_pts_progress(self):
        pipeline = object.__new__(GStreamerPipeline)
        pipeline.channel = 4
        pipeline._last_camera_ntp_ns = None
        pipeline._last_camera_ntp_pts_ns = None
        pipeline._has_frame_ntp = False
        pipeline._publish_camera_ntp = lambda *_args: None

        with patch("builtins.print") as warning:
            pipeline._observe_camera_ntp(Gst.SECOND, Gst.SECOND, 0.0)
            pipeline._observe_camera_ntp(
                Gst.SECOND + 200 * Gst.MSECOND,
                Gst.SECOND + 33 * Gst.MSECOND,
                0.0,
            )

        warning.assert_called_once()
        self.assertIn("Camera channel 4 NTP jumped", warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
