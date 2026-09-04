"""Map camera PTS to a stable host timebase without changing it from DVR NTP."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import time

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from sensors.camera.camera_reference_clock import reference_timestamp_for_buffer


CAMERA_FRAME_RATE = 30
FRAME_PERIOD_NS = Gst.SECOND // CAMERA_FRAME_RATE
UNUSUAL_PTS_GAP_NS = FRAME_PERIOD_NS * 7 // 4
SYSTEM_CLOCK_STEP_NS = Gst.MSECOND


@dataclass(frozen=True)
class FrameTimestampResult:
    captured_at: datetime | None
    media_time_ns: int | None = None
    source: str | None = None
    reason: str | None = None
    receipt_offset_seconds: float | None = None
    reference_clock_offset_seconds: float | None = None
    reference_warning: str | None = None
    camera_ntp_ns: int | None = None
    timing: dict | None = None

    @property
    def valid(self) -> bool:
        return self.captured_at is not None


class FrameTimestampPolicy:
    """Validate PTS and map segment running time onto the host Unix clock."""

    def __init__(self, max_clock_offset_seconds: float = 5.0) -> None:
        self.max_clock_offset_seconds = max_clock_offset_seconds
        self.pipeline = None
        self.stream_epoch = 0
        self._last_pts: int | None = None
        self._pipeline_zero_unix_ns: int | None = None
        self._pipeline_zero_monotonic_ns: int | None = None

    def reset(self, pipeline=None, *, stream_epoch: int = 0) -> None:
        self.pipeline = pipeline
        self.stream_epoch = int(stream_epoch)
        self._last_pts = None
        self._pipeline_zero_unix_ns = None
        self._pipeline_zero_monotonic_ns = None

    @staticmethod
    def _valid_clock_time(value) -> bool:
        return isinstance(value, int) and 0 <= value < Gst.CLOCK_TIME_NONE

    def _running_time_ns(self, sample, pts: int) -> int | None:
        try:
            segment = sample.get_segment()
            running_time = segment.to_running_time(Gst.Format.TIME, pts)
        except (AttributeError, TypeError):
            return None
        return int(running_time) if self._valid_clock_time(running_time) else None

    def _current_running_time_ns(self) -> int | None:
        if self.pipeline is None:
            return None
        clock = self.pipeline.get_clock()
        if clock is None:
            return None
        current_running_time = int(clock.get_time()) - int(self.pipeline.get_base_time())
        return current_running_time if current_running_time >= 0 else None

    def _ensure_anchor(
        self,
        receipt_unix_ns: int,
        receipt_monotonic_ns: int,
        current_running_time_ns: int,
    ) -> None:
        if self._pipeline_zero_unix_ns is not None:
            return
        self._pipeline_zero_unix_ns = receipt_unix_ns - current_running_time_ns
        self._pipeline_zero_monotonic_ns = (
            receipt_monotonic_ns - current_running_time_ns
        )

    @property
    def epoch_metadata(self) -> dict | None:
        if self._pipeline_zero_unix_ns is None:
            return None
        clock_name = None
        if self.pipeline is not None:
            clock = self.pipeline.get_clock()
            if clock is not None:
                clock_name = type(clock).__name__
        return {
            "stream_epoch": self.stream_epoch,
            "pipeline_zero_unix_ns": self._pipeline_zero_unix_ns,
            "pipeline_zero_monotonic_ns": self._pipeline_zero_monotonic_ns,
            "pipeline_clock_type": clock_name,
        }

    def timestamp_for_sample(self, sample) -> FrameTimestampResult:
        buffer = sample.get_buffer()
        if buffer is None:
            return FrameTimestampResult(None, reason="sample has no buffer")

        pts = buffer.pts
        if not self._valid_clock_time(pts):
            return FrameTimestampResult(None, reason="frame has invalid PTS")
        if self._last_pts is not None and pts <= self._last_pts:
            return FrameTimestampResult(
                None,
                reason="frame PTS is duplicated or moved backwards",
            )

        running_time_ns = self._running_time_ns(sample, pts)
        if running_time_ns is None:
            return FrameTimestampResult(
                None,
                reason="frame PTS cannot be mapped to running time",
            )
        receipt_unix_ns = time.time_ns()
        receipt_monotonic_ns = time.monotonic_ns()
        current_running_time_ns = self._current_running_time_ns()
        if current_running_time_ns is None:
            return FrameTimestampResult(None, reason="pipeline clock is unavailable")

        self._ensure_anchor(
            receipt_unix_ns,
            receipt_monotonic_ns,
            current_running_time_ns,
        )
        assert self._pipeline_zero_unix_ns is not None
        assert self._pipeline_zero_monotonic_ns is not None
        media_time_ns = self._pipeline_zero_unix_ns + running_time_ns
        stable_receipt_unix_ns = (
            self._pipeline_zero_unix_ns
            + receipt_monotonic_ns
            - self._pipeline_zero_monotonic_ns
        )
        system_clock_error_ns = receipt_unix_ns - stable_receipt_unix_ns

        reference = reference_timestamp_for_buffer(buffer)
        reference_offset_seconds = None
        reference_warning = None
        if reference.unix_ns is not None:
            reference_offset_seconds = (
                reference.unix_ns - media_time_ns
            ) / Gst.SECOND
            if abs(reference_offset_seconds) > self.max_clock_offset_seconds:
                reference_warning = (
                    "DVR reference clock offset is "
                    f"{reference_offset_seconds:+.3f} seconds"
                )

        previous_pts = self._last_pts
        self._last_pts = int(pts)
        pts_delta_ns = None if previous_pts is None else int(pts - previous_pts)
        unusual_pts_gap = (
            pts_delta_ns is not None and pts_delta_ns > UNUSUAL_PTS_GAP_NS
        )
        flags = []
        if unusual_pts_gap:
            flags.append("unusual_pts_gap")
        if abs(system_clock_error_ns) > SYSTEM_CLOCK_STEP_NS:
            flags.append("system_clock_step")
        if reference.raw_ns is not None and reference.unix_ns is None:
            flags.append("unknown_reference_clock")

        captured_at = datetime.fromtimestamp(
            media_time_ns / Gst.SECOND,
            timezone.utc,
        ).astimezone()
        timing = {
            "stream_epoch": self.stream_epoch,
            "pts_ns": int(pts),
            "pts_delta_ns": pts_delta_ns,
            "running_time_ns": running_time_ns,
            "pipeline_age_ns": current_running_time_ns - running_time_ns,
            "host_realtime_received_ns": receipt_unix_ns,
            "host_monotonic_received_ns": receipt_monotonic_ns,
            "reference_timestamp_raw_ns": reference.raw_ns,
            "reference_clock": reference.reference,
            "camera_ntp_ns": reference.unix_ns,
            "media_time_ns": media_time_ns,
            "large_pts_gap_candidate": unusual_pts_gap,
            "system_clock_error_ns": system_clock_error_ns,
            "flags": flags,
            **(self.epoch_metadata or {}),
        }
        return FrameTimestampResult(
            captured_at,
            media_time_ns=media_time_ns,
            source="host-anchored-pts",
            receipt_offset_seconds=(receipt_unix_ns - media_time_ns) / Gst.SECOND,
            reference_clock_offset_seconds=reference_offset_seconds,
            reference_warning=reference_warning,
            camera_ntp_ns=reference.unix_ns,
            timing=timing,
        )
