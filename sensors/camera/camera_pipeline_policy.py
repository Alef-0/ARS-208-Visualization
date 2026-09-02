"""Camera decoder selection and synchronized frame timestamp policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
try:
    gi.require_version("GstRtp", "1.0")
except ValueError:
    GstRtp = None
else:
    from gi.repository import GstRtp

from gi.repository import GObject, Gst


NTP_UNIX_EPOCH_DELTA_SECONDS = 2_208_988_800
MAX_REFERENCE_CLOCK_OFFSET_SECONDS = 5.0
DECODER_ENVIRONMENT_VARIABLE = "SEGCOM_CAMERA_DECODER"
CAMERA_FRAME_RATE = 30
FRAME_PERIOD_NS = Gst.SECOND // CAMERA_FRAME_RATE
NTP_TRACKING_ERROR_NS = 10 * Gst.MSECOND
NTP_STEP_CONFIRMATIONS = 3
NTP_FILTER_ALPHA = 0.125
NTP_MAX_SLEW_PER_FRAME_NS = Gst.MSECOND


@dataclass(frozen=True)
class CameraDecoderBackend:
    name: str
    required_elements: tuple[str, ...]
    decoder_chain: str


@dataclass(frozen=True)
class FrameTimestampResult:
    captured_at: datetime | None
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


RTX_BACKEND = CameraDecoderBackend(
    name="rtx",
    required_elements=("nvh264dec", "cudaconvert", "cudadownload", "videoconvert"),
    decoder_chain=(
        "nvh264dec ! cudaconvert ! cudadownload ! videoconvert ! "
        "video/x-raw,format=BGR"
    ),
)
ORIN_BACKEND = CameraDecoderBackend(
    name="orin",
    required_elements=("nvv4l2decoder", "nvvidconv", "videoconvert"),
    decoder_chain=(
        "nvv4l2decoder ! nvvidconv ! video/x-raw,format=BGRx ! "
        "videoconvert ! video/x-raw,format=BGR"
    ),
)
CPU_BACKEND = CameraDecoderBackend(
    name="cpu",
    required_elements=("avdec_h264", "videoconvert"),
    decoder_chain="avdec_h264 ! videoconvert ! video/x-raw,format=BGR",
)
BACKENDS = {
    backend.name: backend
    for backend in (RTX_BACKEND, ORIN_BACKEND, CPU_BACKEND)
}


def is_jetson_platform() -> bool:
    if Path("/etc/nv_tegra_release").exists():
        return True
    try:
        compatible = Path("/proc/device-tree/compatible").read_bytes().lower()
    except OSError:
        return False
    return b"nvidia" in compatible or b"tegra" in compatible


def available_decoder_backends(
    preference: str | None = None,
    *,
    factory_find: Callable[[str], object | None] = Gst.ElementFactory.find,
    jetson: bool | None = None,
) -> tuple[CameraDecoderBackend, ...]:
    """Return usable decoders in attempted order, always ending with CPU when present."""

    requested = (preference or os.getenv(DECODER_ENVIRONMENT_VARIABLE, "auto")).lower()
    if requested not in (*BACKENDS, "auto"):
        print(
            f"[DEBUG][CAMERA] Ignoring invalid {DECODER_ENVIRONMENT_VARIABLE}="
            f"{requested!r}; expected auto, rtx, orin, or cpu"
        )
        requested = "auto"

    if requested == "auto":
        use_jetson_order = is_jetson_platform() if jetson is None else jetson
        order = (ORIN_BACKEND, RTX_BACKEND, CPU_BACKEND) if use_jetson_order else (
            RTX_BACKEND,
            ORIN_BACKEND,
            CPU_BACKEND,
        )
    else:
        selected = BACKENDS[requested]
        order = (selected,) if selected is CPU_BACKEND else (selected, CPU_BACKEND)

    available = []
    for backend in order:
        if backend in available:
            continue
        if all(factory_find(element) is not None for element in backend.required_elements):
            available.append(backend)
        elif requested == backend.name:
            print(
                f"[DEBUG][CAMERA] Requested {backend.name} decoder is unavailable; "
                "falling back to CPU"
            )

    if CPU_BACKEND not in available:
        raise RuntimeError("Required CPU GStreamer H.264 decoder elements are unavailable")
    return tuple(available)


def build_camera_pipeline(
    backend: CameraDecoderBackend,
    *,
    display_width: int,
    display_height: int,
    latency_ms: int,
) -> str:
    """Build the low-latency display and full-resolution capture pipeline."""

    return (
        f"rtspsrc name=source latency={latency_ms} protocols=tcp+udp "
        "buffer-mode=1 do-retransmission=true ! "
        f"rtph264depay ! h264parse ! {backend.decoder_chain} ! tee name=video "
        "video. ! queue leaky=downstream max-size-buffers=1 ! videoscale ! "
        f"video/x-raw,format=BGR,width={display_width},height={display_height} ! "
        "appsink name=display_sink emit-signals=true sync=false max-buffers=1 drop=true "
        "video. ! queue name=capture_queue max-size-buffers=30 "
        "max-size-bytes=0 max-size-time=0 ! "
        "video/x-raw,format=BGR ! "
        "appsink name=capture_sink emit-signals=true sync=false"
    )


class FrameTimestampPolicy:
    """Validate PTS and map it to wall time for synchronized saved frames."""

    def __init__(
        self,
        max_clock_offset_seconds: float = MAX_REFERENCE_CLOCK_OFFSET_SECONDS,
    ) -> None:
        self.max_clock_offset_seconds = max_clock_offset_seconds
        self.pipeline = None
        self.rtp_sources: dict[tuple[int, int], object] = {}
        self.rtcp_sender_report_received = False
        self.rtcp_clock_valid = False
        self.latest_sender_report_ntp_ns: int | None = None
        self._last_pts: int | None = None
        self._last_timestamp_ns: int | None = None
        self._anchor_system_ns: int | None = None
        self._anchor_monotonic_ns: int | None = None
        self._anchor_running_ns: int | None = None
        self._ntp_anchor_ns: int | None = None
        self._ntp_anchor_running_ns: int | None = None
        self._camera_to_host_offset_ns: int | None = None
        self._filtered_ntp_correction_ns = 0
        self._target_ntp_correction_ns = 0
        self._pending_ntp_step_ns: int | None = None
        self._pending_ntp_step_count = 0
        self._last_reference_ntp_ns: int | None = None
        self._last_reference_running_ns: int | None = None
        self._timing_epoch = 1
        self._ntp_correction_generation = 0

    def reset(self, pipeline=None) -> None:
        self.pipeline = pipeline
        self.rtp_sources.clear()
        self.rtcp_sender_report_received = False
        self.rtcp_clock_valid = False
        self.latest_sender_report_ntp_ns = None
        self._last_pts = None
        self._last_timestamp_ns = None
        self._anchor_system_ns = None
        self._anchor_monotonic_ns = None
        self._anchor_running_ns = None
        self._ntp_anchor_ns = None
        self._ntp_anchor_running_ns = None
        self._camera_to_host_offset_ns = None
        self._filtered_ntp_correction_ns = 0
        self._target_ntp_correction_ns = 0
        self._pending_ntp_step_ns = None
        self._pending_ntp_step_count = 0
        self._last_reference_ntp_ns = None
        self._last_reference_running_ns = None
        self._timing_epoch = 1
        self._ntp_correction_generation = 0

    @staticmethod
    def configure_rtsp_source(source) -> None:
        """Enable RTCP and reference timestamp metadata when the plugin supports it."""

        for property_name, value in (
            ("do-rtcp", True),
            ("drop-on-latency", True),
            ("add-reference-timestamp-meta", True),
        ):
            if source.find_property(property_name) is not None:
                source.set_property(property_name, value)

    def attach_rtsp_source(self, source) -> None:
        self.configure_rtsp_source(source)
        if GObject.signal_lookup("new-manager", type(source)):
            source.connect("new-manager", self._on_new_manager)

    def _on_new_manager(self, _source, manager) -> None:
        manager.connect("on-new-ssrc", self._on_new_ssrc)

    def _on_new_ssrc(self, manager, session_id: int, ssrc: int) -> None:
        try:
            session = manager.emit("get-internal-session", session_id)
            rtp_source = session.emit("get-source-by-ssrc", ssrc)
        except (AttributeError, TypeError):
            return
        if rtp_source is not None:
            self.rtp_sources[(session_id, ssrc)] = rtp_source

    def check_rtcp_stats(self) -> bool:
        if GstRtp is None:
            return True
        for rtp_source in tuple(self.rtp_sources.values()):
            try:
                stats = rtp_source.get_property("stats")
                if not bool(stats.get_value("have-sr")):
                    continue
                ntp_time = int(stats.get_value("sr-ntptime"))
            except (AttributeError, KeyError, TypeError, ValueError):
                continue
            self.rtcp_sender_report_received = True
            if ntp_time == 0:
                continue
            unix_nanoseconds = int(GstRtp.rtcp_ntp_to_unix(ntp_time))
            self.latest_sender_report_ntp_ns = unix_nanoseconds
            offset_seconds = unix_nanoseconds / Gst.SECOND - time.time()
            clock_valid = abs(offset_seconds) <= self.max_clock_offset_seconds
            self.rtcp_clock_valid = self.rtcp_clock_valid or clock_valid
        return True

    @staticmethod
    def _valid_clock_time(value) -> bool:
        return isinstance(value, int) and 0 <= value < Gst.CLOCK_TIME_NONE

    @staticmethod
    def _reference_timestamp_ns(buffer, expected_timestamp_ns: int) -> int | None:
        reference_meta = buffer.get_reference_timestamp_meta(None)
        if reference_meta is None:
            return None

        timestamp_ns = int(reference_meta.timestamp)
        if timestamp_ns <= 0 or timestamp_ns >= Gst.CLOCK_TIME_NONE:
            return None
        candidates = tuple(
            candidate
            for candidate in (
                timestamp_ns,
                timestamp_ns - NTP_UNIX_EPOCH_DELTA_SECONDS * Gst.SECOND,
            )
            if candidate >= 0
        )
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: abs(candidate - expected_timestamp_ns))

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

    def _wall_time_from_running_time(
        self,
        running_time_ns: int,
        receipt_system_ns: int,
        receipt_monotonic_ns: int,
        current_running_time_ns: int,
    ) -> int:
        if self._anchor_system_ns is None or self._anchor_running_ns is None:
            self._anchor_system_ns = receipt_system_ns
            self._anchor_monotonic_ns = receipt_monotonic_ns
            self._anchor_running_ns = current_running_time_ns
        return (
            self._anchor_system_ns
            + running_time_ns
            - self._anchor_running_ns
        )

    def _stable_wall_time_at_receipt(self, receipt_monotonic_ns: int) -> int:
        assert self._anchor_system_ns is not None
        assert self._anchor_monotonic_ns is not None
        return (
            self._anchor_system_ns
            + receipt_monotonic_ns
            - self._anchor_monotonic_ns
        )

    @staticmethod
    def _slew_toward(current: int, target: int, limit: int) -> int:
        difference = target - current
        if abs(difference) <= limit:
            return target
        return current + (limit if difference > 0 else -limit)

    def _discipline_ntp(
        self,
        running_time_ns: int,
        pts_time_ns: int,
        reference_ns: int | None,
    ) -> dict:
        ntp_status = "unavailable"
        ntp_valid = False
        correction_observation_used = False
        ntp_residual_ns = None
        ntp_progression_error_ns = None

        if self._ntp_anchor_ns is None and reference_ns is not None:
            self._ntp_anchor_ns = reference_ns
            self._ntp_anchor_running_ns = running_time_ns
            self._camera_to_host_offset_ns = pts_time_ns - reference_ns
            self._last_reference_ntp_ns = reference_ns
            self._last_reference_running_ns = running_time_ns
            ntp_status = "anchor"
            ntp_valid = True
            correction_observation_used = True

        predicted_without_correction_ns = None
        predicted_camera_ntp_ns = None
        if self._ntp_anchor_ns is not None and self._ntp_anchor_running_ns is not None:
            predicted_without_correction_ns = (
                self._ntp_anchor_ns
                + running_time_ns
                - self._ntp_anchor_running_ns
            )

            if reference_ns is None:
                ntp_status = "missing-predicted"
            elif ntp_status != "anchor":
                if (
                    self._last_reference_ntp_ns is not None
                    and self._last_reference_running_ns is not None
                ):
                    reference_delta_ns = reference_ns - self._last_reference_ntp_ns
                    running_delta_ns = running_time_ns - self._last_reference_running_ns
                    ntp_progression_error_ns = reference_delta_ns - running_delta_ns
                    if reference_delta_ns <= 0:
                        ntp_status = "invalid-backward-or-duplicate"
                    else:
                        ntp_valid = True
                        self._last_reference_ntp_ns = reference_ns
                        self._last_reference_running_ns = running_time_ns
                else:
                    ntp_valid = True
                    self._last_reference_ntp_ns = reference_ns
                    self._last_reference_running_ns = running_time_ns

                if ntp_valid:
                    desired_correction_ns = reference_ns - predicted_without_correction_ns
                    correction_error_ns = (
                        desired_correction_ns - self._target_ntp_correction_ns
                    )
                    ntp_residual_ns = (
                        reference_ns
                        - predicted_without_correction_ns
                        - self._filtered_ntp_correction_ns
                    )
                    if abs(correction_error_ns) <= NTP_TRACKING_ERROR_NS:
                        adjustment_ns = round(
                            correction_error_ns * NTP_FILTER_ALPHA
                        )
                        self._target_ntp_correction_ns += adjustment_ns
                        self._pending_ntp_step_ns = None
                        self._pending_ntp_step_count = 0
                        ntp_status = "tracking"
                        correction_observation_used = True
                    else:
                        same_step = (
                            self._pending_ntp_step_ns is not None
                            and abs(
                                desired_correction_ns - self._pending_ntp_step_ns
                            ) <= NTP_TRACKING_ERROR_NS
                        )
                        if same_step:
                            self._pending_ntp_step_count += 1
                        else:
                            self._pending_ntp_step_ns = desired_correction_ns
                            self._pending_ntp_step_count = 1
                        ntp_status = (
                            "step-candidate-"
                            f"{self._pending_ntp_step_count}/{NTP_STEP_CONFIRMATIONS}"
                        )
                        if self._pending_ntp_step_count >= NTP_STEP_CONFIRMATIONS:
                            self._target_ntp_correction_ns = desired_correction_ns
                            self._pending_ntp_step_ns = None
                            self._pending_ntp_step_count = 0
                            self._ntp_correction_generation += 1
                            ntp_status = "step-confirmed-slewing"
                            correction_observation_used = True

            self._filtered_ntp_correction_ns = self._slew_toward(
                self._filtered_ntp_correction_ns,
                self._target_ntp_correction_ns,
                NTP_MAX_SLEW_PER_FRAME_NS,
            )
            predicted_camera_ntp_ns = (
                predicted_without_correction_ns
                + self._filtered_ntp_correction_ns
            )

        hybrid_time_ns = pts_time_ns
        if (
            predicted_camera_ntp_ns is not None
            and self._camera_to_host_offset_ns is not None
        ):
            hybrid_time_ns = (
                predicted_camera_ntp_ns + self._camera_to_host_offset_ns
            )

        return {
            "camera_ntp_valid": ntp_valid,
            "camera_ntp_status": ntp_status,
            "ntp_correction_observation_used": correction_observation_used,
            "predicted_camera_ntp_ns": predicted_camera_ntp_ns,
            "ntp_residual_ns": ntp_residual_ns,
            "ntp_progression_error_ns": ntp_progression_error_ns,
            "filtered_ntp_correction_ns": self._filtered_ntp_correction_ns,
            "target_ntp_correction_ns": self._target_ntp_correction_ns,
            "camera_to_host_offset_ns": self._camera_to_host_offset_ns,
            "hybrid_time_ns": hybrid_time_ns,
            "ntp_correction_generation": self._ntp_correction_generation,
        }

    def timestamp_for_sample(self, sample) -> FrameTimestampResult:
        buffer = sample.get_buffer()
        if buffer is None:
            return FrameTimestampResult(None, reason="sample has no buffer")

        pts = buffer.pts
        if not self._valid_clock_time(pts):
            return FrameTimestampResult(None, reason="frame has invalid PTS")
        if self._last_pts is not None and pts <= self._last_pts:
            return FrameTimestampResult(None, reason="frame PTS is duplicated or moved backwards")

        running_time_ns = self._running_time_ns(sample, pts)
        if running_time_ns is None:
            return FrameTimestampResult(None, reason="frame PTS cannot be mapped to running time")
        receipt_system_ns = time.time_ns()
        receipt_monotonic_ns = time.monotonic_ns()
        current_running_time_ns = self._current_running_time_ns()
        if current_running_time_ns is None:
            return FrameTimestampResult(None, reason="pipeline clock is unavailable")
        pts_time_ns = self._wall_time_from_running_time(
            running_time_ns,
            receipt_system_ns,
            receipt_monotonic_ns,
            current_running_time_ns,
        )
        stable_receipt_time_ns = self._stable_wall_time_at_receipt(
            receipt_monotonic_ns
        )
        reference_ns = self._reference_timestamp_ns(buffer, pts_time_ns)
        ntp_timing = self._discipline_ntp(
            running_time_ns,
            pts_time_ns,
            reference_ns,
        )
        timestamp_ns = int(ntp_timing["hybrid_time_ns"])
        monotonic_clamped = False

        if self._last_timestamp_ns is not None and timestamp_ns <= self._last_timestamp_ns:
            timestamp_ns = self._last_timestamp_ns + 1
            monotonic_clamped = True

        previous_pts = self._last_pts
        self._last_pts = pts
        self._last_timestamp_ns = timestamp_ns
        captured_at = datetime.fromtimestamp(timestamp_ns / Gst.SECOND, timezone.utc).astimezone()
        receipt_offset_seconds = (receipt_system_ns - timestamp_ns) / Gst.SECOND
        reference_clock_offset_seconds = None
        reference_warning = None
        if reference_ns is not None:
            reference_clock_offset_seconds = (reference_ns - pts_time_ns) / Gst.SECOND
            if abs(reference_clock_offset_seconds) > self.max_clock_offset_seconds:
                reference_warning = (
                    "DVR RTCP/NTP clock offset is "
                    f"{reference_clock_offset_seconds:+.3f} seconds"
                )
        pts_delta_ns = None if previous_pts is None else int(pts - previous_pts)
        estimated_missing_frames = 0
        if pts_delta_ns is not None and pts_delta_ns > FRAME_PERIOD_NS * 3 // 2:
            estimated_missing_frames = max(
                round(pts_delta_ns / FRAME_PERIOD_NS) - 1,
                0,
            )
        timing = {
            "timing_epoch": self._timing_epoch,
            "pts_ns": int(pts),
            "pts_delta_ns": pts_delta_ns,
            "estimated_missing_frames": estimated_missing_frames,
            "running_time_ns": running_time_ns,
            "pipeline_running_time_ns": current_running_time_ns,
            "pipeline_age_ns": current_running_time_ns - running_time_ns,
            "host_realtime_received_ns": receipt_system_ns,
            "host_monotonic_received_ns": receipt_monotonic_ns,
            "stable_host_time_at_receipt_ns": stable_receipt_time_ns,
            "system_clock_error_ns": receipt_system_ns - stable_receipt_time_ns,
            "pts_time_ns": pts_time_ns,
            "camera_ntp_ns": reference_ns,
            "camera_ntp_offset_ns": (
                None if reference_ns is None else reference_ns - pts_time_ns
            ),
            **ntp_timing,
            "attempted_capture_time_ns": timestamp_ns,
            "timestamp_monotonic_clamped": monotonic_clamped,
        }
        return FrameTimestampResult(
            captured_at,
            source=(
                "pts-disciplined-by-ntp"
                if ntp_timing["predicted_camera_ntp_ns"] is not None
                else "pts-only"
            ),
            receipt_offset_seconds=receipt_offset_seconds,
            reference_clock_offset_seconds=reference_clock_offset_seconds,
            reference_warning=reference_warning,
            camera_ntp_ns=reference_ns,
            timing=timing,
        )
