from datetime import datetime
import queue
import signal
import socket
import threading
import time

import cv2 as cv
import gi
import numpy as np

from CAPTURE import CameraSnapshotRecorder

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)

CAMERA_PIPELINE_LATENCY_MS = 250
MAX_PIPELINE_ATTEMPTS = 3
FIRST_FRAME_TIMEOUT_SECONDS = 5.0
PIPELINE_RETRY_DELAY_SECONDS = 0.5
_RESULT_FAILURE = "failure"
_RESULT_RESTART = "restart"
_RESULT_CLOSED = "closed"


class GStreamerPipeline:
    def __init__(self, conn, pool, shutdown_event):
        self.pipeline = None
        self.main_loop = None
        self.frames = queue.Queue(maxsize=1)
        self.channel = 2
        self.communicate = conn
        self.pool = pool
        self.shutdown_event = shutdown_event
        self.connected = False
        self.source_ids = []
        self.first_frame_received = False
        self.attempt_started = 0.0
        self.exit_reason = _RESULT_FAILURE
        self.channel_changed = False
        self.snapshot_recorder = CameraSnapshotRecorder(self._report_snapshot)
        self._manual_snapshot_lock = threading.Lock()
        self._pending_manual_snapshot: dict | None = None

    @staticmethod
    def create_url(channel):
        return f"rtsp://admin:l1v3user5@192.168.1.108:554/cam/realmonitor?channel={channel}&subtype=0"

    def _put_status(self, message, payload, *, timeout=0.2):
        try:
            self.pool.put((message, payload), timeout=timeout)
        except queue.Full:
            pass

    def _report_snapshot(self, payload):
        self._put_status("camera_snapshot", payload)

    def _start_snapshot_recording(self, folders):
        try:
            self.snapshot_recorder.start(folders)
            self._put_status("camera_recording_state", {"active": True})
        except Exception as error:
            self._put_status("camera_recording_error", str(error))
            self._put_status("camera_recording_state", {"active": False})

    def _stop_snapshot_recording(self):
        try:
            count = self.snapshot_recorder.stop()
            self._put_status(
                "camera_recording_state",
                {"active": False, "count": count},
            )
        except Exception as error:
            self._put_status("camera_recording_error", str(error))
            self._put_status("camera_recording_state", {"active": False})

    def _fail_manual_snapshot(self, message):
        with self._manual_snapshot_lock:
            request = self._pending_manual_snapshot
            self._pending_manual_snapshot = None
        if request is None:
            return
        self._put_status(
            "manual_snapshot_error",
            {
                "request_id": request.get("request_id"),
                "message": message,
            },
        )

    def _queue_manual_snapshot(self, value):
        if not self.connected:
            self._put_status(
                "manual_snapshot_error",
                {
                    "request_id": value.get("request_id"),
                    "message": "Connect the camera before taking a snapshot",
                },
            )
            return False

        channel = int(value.get("channel", 0))
        if channel not in (1, 2, 3):
            self._put_status(
                "manual_snapshot_error",
                {
                    "request_id": value.get("request_id"),
                    "message": f"Unsupported camera group: {channel}",
                },
            )
            return False

        request = dict(value)
        request["restore_channel"] = self.channel
        with self._manual_snapshot_lock:
            if self._pending_manual_snapshot is not None:
                self._put_status(
                    "manual_snapshot_error",
                    {
                        "request_id": value.get("request_id"),
                        "message": "A camera snapshot is already pending",
                    },
                )
                return False
            self._pending_manual_snapshot = request

        if channel != self.channel:
            self.channel = channel
            self.channel_changed = True
            self.exit_reason = _RESULT_RESTART
            return True
        return False

    def _restore_channel_after_snapshot(self, channel):
        if channel != self.channel:
            self.channel = channel
            self.channel_changed = True
            self.exit_reason = _RESULT_RESTART
            if self.main_loop:
                self.main_loop.quit()
        return GLib.SOURCE_REMOVE

    def _emit_manual_snapshot(self, frame, captured_at):
        with self._manual_snapshot_lock:
            request = self._pending_manual_snapshot
            if request is None or int(request["channel"]) != self.channel:
                return
            self._pending_manual_snapshot = None

        success, encoded = cv.imencode(".jpg", frame)
        if not success:
            self._put_status(
                "manual_snapshot_error",
                {
                    "request_id": request.get("request_id"),
                    "message": "Could not encode the camera snapshot",
                },
            )
            return

        self._put_status(
            "manual_snapshot_frame",
            {
                "request_id": request.get("request_id"),
                "folder": request.get("folder"),
                "channel": int(request["channel"]),
                "captured_at": captured_at.isoformat(timespec="microseconds"),
                "image_bytes": encoded.tobytes(),
            },
            timeout=1.0,
        )

        restore_channel = int(request.get("restore_channel", self.channel))
        if restore_channel != self.channel:
            GLib.idle_add(self._restore_channel_after_snapshot, restore_channel)

    @staticmethod
    def _sample_to_frame(sample):
        buffer = sample.get_buffer()
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return None, None
        try:
            caps = sample.get_caps().get_structure(0)
            width = caps.get_value("width")
            height = caps.get_value("height")
            frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape(height, width, 3).copy()
            return frame, datetime.now().astimezone()
        finally:
            buffer.unmap(map_info)

    def on_new_display_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        frame, _ = self._sample_to_frame(sample)
        if frame is None:
            return Gst.FlowReturn.ERROR
        try:
            self.frames.put_nowait(frame)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            self.frames.put_nowait(frame)
        return Gst.FlowReturn.OK

    def on_new_capture_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        frame, captured_at = self._sample_to_frame(sample)
        if frame is None or captured_at is None:
            return Gst.FlowReturn.ERROR
        self.first_frame_received = True
        self.snapshot_recorder.submit(frame, captured_at=captured_at)
        self._emit_manual_snapshot(frame, captured_at)
        return Gst.FlowReturn.OK

    def on_message(self, _bus, message):
        if message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            self.exit_reason = _RESULT_FAILURE
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                print(f"GStreamer error: {error}; {debug}")
            if self.main_loop:
                self.main_loop.quit()

    def process_commands(self):
        restart = False
        try:
            while self.communicate.poll():
                event, value = self.communicate.recv()
                if event == "STOP":
                    self._stop_snapshot_recording()
                    self._fail_manual_snapshot("Camera process stopped before taking the snapshot")
                    self.shutdown_event.set()
                    self.connected = False
                    self.exit_reason = _RESULT_CLOSED
                    restart = True
                elif event == "choose" and value != self.channel:
                    self.channel = value
                    self.channel_changed = True
                    if self.connected:
                        self.exit_reason = _RESULT_RESTART
                        restart = True
                elif event == "conn_cam":
                    if self.connected:
                        self.connected = False
                        self.exit_reason = _RESULT_CLOSED
                        self._fail_manual_snapshot("Camera disconnected before taking the snapshot")
                        self._put_status("change_cam", False)
                        restart = True
                    else:
                        try:
                            with socket.create_connection(("192.168.1.108", 554), timeout=2):
                                self.connected = True
                                self._put_status("change_cam", True)
                        except OSError:
                            self._put_status("change_cam", False)
                elif event == "record_start":
                    self._start_snapshot_recording(value.get("folders", {}))
                elif event == "record_stop":
                    self._stop_snapshot_recording()
                elif event == "snapshot_capture":
                    restart = self._queue_manual_snapshot(value) or restart
        except (EOFError, OSError):
            self.shutdown_event.set()
            self.connected = False
            self.exit_reason = _RESULT_CLOSED
            restart = True

        snapshot_error = self.snapshot_recorder.poll_error()
        if snapshot_error is not None and self.snapshot_recorder.active:
            self._stop_snapshot_recording()

        if (restart or self.shutdown_event.is_set()) and self.main_loop:
            self.main_loop.quit()
        return GLib.SOURCE_CONTINUE

    def display_latest_frame(self):
        try:
            frame = self.frames.get_nowait()
        except queue.Empty:
            return GLib.SOURCE_CONTINUE
        cv.imshow("CAMERA", frame)
        cv.waitKey(1)
        return GLib.SOURCE_CONTINUE

    def check_first_frame(self):
        if self.first_frame_received:
            return GLib.SOURCE_CONTINUE
        if time.monotonic() - self.attempt_started < FIRST_FRAME_TIMEOUT_SECONDS:
            return GLib.SOURCE_CONTINUE
        print(
            f"Camera channel {self.channel} did not produce a frame within "
            f"{FIRST_FRAME_TIMEOUT_SECONDS:.1f} seconds"
        )
        self.exit_reason = _RESULT_FAILURE
        if self.main_loop:
            self.main_loop.quit()
        return GLib.SOURCE_CONTINUE

    def _clear_frames(self):
        while True:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                return

    def _remove_sources(self):
        for source_id in self.source_ids:
            if source_id:
                GLib.source_remove(source_id)
        self.source_ids.clear()

    @staticmethod
    def _destroy_window():
        try:
            cv.destroyWindow("CAMERA")
            cv.waitKey(1)
        except cv.error:
            pass

    def run(self):
        pipeline_str = (
            f"rtspsrc name=source latency={CAMERA_PIPELINE_LATENCY_MS} protocols=tcp ! "
            "rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! tee name=video "
            "video. ! queue leaky=downstream max-size-buffers=1 ! videoscale ! "
            "video/x-raw,format=BGR,width=800,height=600 ! "
            "appsink name=display_sink emit-signals=true sync=false max-buffers=1 drop=true "
            "video. ! queue leaky=downstream max-size-buffers=1 ! video/x-raw,format=BGR ! "
            "appsink name=capture_sink emit-signals=true sync=false max-buffers=1 drop=true"
        )
        bus = None
        self._clear_frames()
        self.first_frame_received = False
        self.attempt_started = time.monotonic()
        self.exit_reason = _RESULT_FAILURE

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            source = self.pipeline.get_by_name("source")
            display_sink = self.pipeline.get_by_name("display_sink")
            capture_sink = self.pipeline.get_by_name("capture_sink")
            source.set_property("location", self.create_url(self.channel))
            display_sink.connect("new-sample", self.on_new_display_sample)
            capture_sink.connect("new-sample", self.on_new_capture_sample)
            bus = self.pipeline.get_bus()
            bus.add_signal_watch()
            bus.connect("message", self.on_message)
            self.main_loop = GLib.MainLoop()

            self.source_ids = [
                GLib.timeout_add(10, self.process_commands),
                GLib.timeout_add(10, self.display_latest_frame),
                GLib.timeout_add(100, self.check_first_frame),
            ]

            if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                print(f"GStreamer could not start camera channel {self.channel}")
                return self.exit_reason, self.first_frame_received

            self.main_loop.run()
            return self.exit_reason, self.first_frame_received
        except Exception as error:
            print(f"Camera pipeline failure: {error}")
            return _RESULT_FAILURE, self.first_frame_received
        finally:
            self._remove_sources()
            if bus is not None:
                bus.remove_signal_watch()
            if self.pipeline is not None:
                self.pipeline.set_state(Gst.State.NULL)
            self._destroy_window()
            self.pipeline = None
            self.main_loop = None


def gstreamer_main(connection, pool, shutdown_event):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())
    pipeline = GStreamerPipeline(connection, pool, shutdown_event)
    failed_attempts = 0

    try:
        while not shutdown_event.is_set():
            pipeline.process_commands()
            if pipeline.channel_changed:
                failed_attempts = 0
                pipeline.channel_changed = False
            if not pipeline.connected:
                failed_attempts = 0
                shutdown_event.wait(0.05)
                continue

            result, received_frame = pipeline.run()
            if result in (_RESULT_RESTART, _RESULT_CLOSED):
                failed_attempts = 0
                continue
            if shutdown_event.is_set() or not pipeline.connected:
                continue

            if received_frame:
                failed_attempts = 0
            failed_attempts += 1
            if failed_attempts >= MAX_PIPELINE_ATTEMPTS:
                print(
                    f"Camera channel {pipeline.channel} failed after "
                    f"{MAX_PIPELINE_ATTEMPTS} attempts"
                )
                pipeline.connected = False
                pipeline._fail_manual_snapshot("Camera pipeline failed before taking the snapshot")
                pipeline._put_status("change_cam", False)
                failed_attempts = 0
                continue

            shutdown_event.wait(PIPELINE_RETRY_DELAY_SECONDS)
    finally:
        pipeline._stop_snapshot_recording()
        pipeline._fail_manual_snapshot("Camera process stopped before taking the snapshot")
        pipeline._remove_sources()
        if pipeline.pipeline:
            pipeline.pipeline.set_state(Gst.State.NULL)
        cv.destroyAllWindows()
