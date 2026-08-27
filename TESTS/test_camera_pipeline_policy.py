from datetime import datetime, timezone
import time
import unittest
from unittest.mock import patch

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from CAMERA.camera_pipeline_policy import (
    CPU_BACKEND,
    FrameTimestampResult,
    FrameTimestampPolicy,
    ORIN_BACKEND,
    RTX_BACKEND,
    available_decoder_backends,
    build_camera_pipeline,
)
from CAMERA.camera_gstreamer import GStreamerPipeline


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
        self.assertIn("width=1280,height=720", description)
        self.assertEqual(description.count("max-buffers=1 drop=true"), 2)

    def test_invalid_pts_is_rejected(self):
        policy = FrameTimestampPolicy()
        result = policy.timestamp_for_sample(FakeSample(FakeBuffer(Gst.CLOCK_TIME_NONE)))
        self.assertFalse(result.valid)
        self.assertIn("invalid PTS", result.reason)

    def test_valid_pts_uses_pipeline_clock_when_ntp_meta_is_absent(self):
        running_time = 10 * Gst.SECOND
        policy = FrameTimestampPolicy()
        policy.reset(FakePipeline(running_time))
        result = policy.timestamp_for_sample(
            FakeSample(FakeBuffer(running_time - 100 * Gst.MSECOND))
        )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "pipeline-clock")
        self.assertLess(abs(result.captured_at.timestamp() - (time.time() - 0.1)), 0.1)

    def test_plausible_reference_timestamp_is_diagnostic_only(self):
        running_time = Gst.SECOND
        host_time_ns = 100 * Gst.SECOND
        policy = FrameTimestampPolicy()
        policy.reset(FakePipeline(running_time))
        with patch(
            "CAMERA.camera_pipeline_policy.time.time_ns",
            return_value=host_time_ns,
        ):
            result = policy.timestamp_for_sample(
                FakeSample(FakeBuffer(running_time, reference_timestamp=host_time_ns))
            )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "pipeline-clock")
        self.assertEqual(result.reference_clock_offset_seconds, 0.0)

    def test_implausible_reference_timestamp_falls_back_to_pipeline_clock(self):
        policy = FrameTimestampPolicy(max_clock_offset_seconds=1.0)
        policy.reset(FakePipeline(Gst.SECOND))
        host_time_ns = 100 * Gst.SECOND
        reference_time_ns = host_time_ns + 25 * Gst.SECOND
        with patch(
            "CAMERA.camera_pipeline_policy.time.time_ns",
            return_value=host_time_ns,
        ):
            result = policy.timestamp_for_sample(
                FakeSample(FakeBuffer(Gst.SECOND, reference_timestamp=reference_time_ns))
            )
        self.assertTrue(result.valid)
        self.assertEqual(result.source, "pipeline-clock")
        self.assertAlmostEqual(result.reference_clock_offset_seconds, 25.0)
        self.assertIn("ignoring DVR", result.reference_warning)

    def test_pipeline_clock_mapping_uses_a_fixed_anchor(self):
        pipeline = FakePipeline(10 * Gst.SECOND)
        policy = FrameTimestampPolicy()
        policy.reset(pipeline)
        with patch(
            "CAMERA.camera_pipeline_policy.time.time_ns",
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
        manual = []
        pipeline._emit_manual_snapshot = lambda *args: manual.append(args)

        result = pipeline.on_new_capture_sample(FakeSink(object()))

        self.assertEqual(result, Gst.FlowReturn.OK)
        self.assertEqual(submitted, [((frame,), {"captured_at": captured_at})])
        self.assertEqual(manual, [(frame, captured_at)])


if __name__ == "__main__":
    unittest.main()
