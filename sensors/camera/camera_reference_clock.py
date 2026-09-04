"""Observe RTCP, RTP jitter-buffer statistics, and frame reference clocks."""

from __future__ import annotations

from dataclasses import dataclass
import time

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
JITTER_COUNTER_FIELDS = (
    "num-pushed",
    "num-lost",
    "num-late",
    "num-duplicates",
    "rtx-count",
    "rtx-success-count",
)


@dataclass(frozen=True)
class ReferenceTimestamp:
    raw_ns: int | None = None
    unix_ns: int | None = None
    reference: str | None = None


def reference_timestamp_for_buffer(buffer) -> ReferenceTimestamp:
    """Read reference metadata and convert only a declared NTP/Unix clock."""

    reference_meta = buffer.get_reference_timestamp_meta(None)
    if reference_meta is None:
        return ReferenceTimestamp()

    raw_ns = int(reference_meta.timestamp)
    if raw_ns <= 0 or raw_ns >= Gst.CLOCK_TIME_NONE:
        return ReferenceTimestamp()
    try:
        reference = reference_meta.reference.to_string()
    except AttributeError:
        reference = None

    unix_ns = None
    if reference is not None and reference.startswith("timestamp/x-ntp"):
        candidate = raw_ns - NTP_UNIX_EPOCH_DELTA_SECONDS * Gst.SECOND
        unix_ns = candidate if candidate >= 0 else None
    elif reference is not None and reference.startswith("timestamp/x-unix"):
        unix_ns = raw_ns
    return ReferenceTimestamp(raw_ns=raw_ns, unix_ns=unix_ns, reference=reference)


class ReferenceClockObserver:
    """Collect sparse clock events and aggregate transport counters."""

    def __init__(self) -> None:
        self.stream_epoch = 0
        self.rtp_sources: dict[tuple[int, int], object] = {}
        self.jitterbuffers: list[object] = []
        self.latest_sender_report_ntp_ns: int | None = None
        self._last_sender_reports: dict[tuple[int, int], tuple[int, int]] = {}
        self._last_transport_stats: dict[str, int] = {}
        self._events: list[dict] = []

    def reset(self, stream_epoch: int) -> None:
        self.stream_epoch = int(stream_epoch)
        self.rtp_sources.clear()
        self.jitterbuffers.clear()
        self.latest_sender_report_ntp_ns = None
        self._last_sender_reports.clear()
        self._last_transport_stats.clear()
        self._events.clear()

    def attach_rtsp_source(self, source) -> None:
        for property_name, value in (
            ("do-rtcp", True),
            ("drop-on-latency", True),
            ("add-reference-timestamp-meta", True),
        ):
            if source.find_property(property_name) is not None:
                source.set_property(property_name, value)
        if GObject.signal_lookup("new-manager", type(source)):
            source.connect("new-manager", self._on_new_manager)

    def _on_new_manager(self, _source, manager) -> None:
        manager.connect("on-new-ssrc", self._on_new_ssrc)
        manager.connect("new-jitterbuffer", self._on_new_jitterbuffer)

    def _on_new_ssrc(self, manager, session_id: int, ssrc: int) -> None:
        try:
            session = manager.emit("get-internal-session", session_id)
            rtp_source = session.emit("get-source-by-ssrc", ssrc)
        except (AttributeError, TypeError):
            return
        if rtp_source is None:
            return
        key = (int(session_id), int(ssrc))
        self.rtp_sources[key] = rtp_source
        self._events.append({
            "event": "new_ssrc",
            "stream_epoch": self.stream_epoch,
            "session_id": key[0],
            "ssrc": key[1],
            "received_monotonic_ns": time.monotonic_ns(),
        })

    def _on_new_jitterbuffer(self, _source, jitterbuffer, *_unused) -> None:
        self.jitterbuffers.append(jitterbuffer)
        for property_name, value in (("do-lost", True), ("post-drop-messages", True)):
            if jitterbuffer.find_property(property_name) is not None:
                jitterbuffer.set_property(property_name, value)

    @staticmethod
    def _stats_value(stats, field: str, default=0):
        try:
            value = stats.get_value(field)
        except (AttributeError, KeyError, TypeError):
            return default
        return default if value is None else value

    def poll(self) -> tuple[dict, ...]:
        if GstRtp is not None:
            for key, rtp_source in tuple(self.rtp_sources.items()):
                try:
                    stats = rtp_source.get_property("stats")
                    if not bool(stats.get_value("have-sr")):
                        continue
                    ntp_raw = int(stats.get_value("sr-ntptime"))
                    rtp_timestamp = int(stats.get_value("sr-rtptime"))
                except (AttributeError, KeyError, TypeError, ValueError):
                    continue
                if ntp_raw == 0:
                    continue
                report = (ntp_raw, rtp_timestamp)
                if self._last_sender_reports.get(key) == report:
                    continue
                self._last_sender_reports[key] = report
                unix_ns = int(GstRtp.rtcp_ntp_to_unix(ntp_raw))
                self.latest_sender_report_ntp_ns = unix_ns
                self._events.append({
                    "event": "rtcp_sender_report",
                    "stream_epoch": self.stream_epoch,
                    "session_id": key[0],
                    "ssrc": key[1],
                    "rtp_timestamp": rtp_timestamp,
                    "ntp_raw": ntp_raw,
                    "ntp_unix_ns": unix_ns,
                    "received_monotonic_ns": time.monotonic_ns(),
                })

        current_stats = self.transport_stats()
        changed_loss = any(
            current_stats.get(field.replace("-", "_"), 0)
            != self._last_transport_stats.get(field.replace("-", "_"), 0)
            for field in JITTER_COUNTER_FIELDS[1:]
        )
        if changed_loss:
            self._events.append({
                "event": "transport_counters",
                "stream_epoch": self.stream_epoch,
                "received_monotonic_ns": time.monotonic_ns(),
                **current_stats,
            })
        self._last_transport_stats = current_stats
        events = tuple(self._events)
        self._events.clear()
        return events

    def transport_stats(self) -> dict[str, int]:
        totals = {field.replace("-", "_"): 0 for field in JITTER_COUNTER_FIELDS}
        for jitterbuffer in tuple(self.jitterbuffers):
            try:
                stats = jitterbuffer.get_property("stats")
            except (AttributeError, TypeError):
                continue
            for field in JITTER_COUNTER_FIELDS:
                totals[field.replace("-", "_")] += int(
                    self._stats_value(stats, field, 0)
                )
        return totals
