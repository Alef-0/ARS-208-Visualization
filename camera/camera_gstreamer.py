import queue
import signal
import socket
import time

import cv2 as cv
import gi
import numpy as np

from recording import CameraSnapshotRecorder

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)

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

    @staticmethod
    def create_url(channel):
        return f"rtsp://admin:l1v3user5@192.168.1.108:554/cam/realmonitor?channel={channel}&subtype=0"

    def _put_status(self, message, payload):
        try:
            self.pool.put((message, payload), timeout=0.2)
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

    def on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR
        try:
            caps = sample.get_caps().get_structure(0)
            width = caps.get_value("width")
            height = caps.get_value("height")
            frame = np.frombuffer(map_info.data, dtype=np.uint8).reshape(height, width, 3).copy()
            self.first_frame_received = True
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                self.frames.put_nowait(frame)
            self.snapshot_recorder.submit(frame)
        finally:
            buffer.unmap(map_info)
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
            "rtspsrc name=source latency=100 protocols=tcp ! "
            "rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! videoscale ! "
            "video/x-raw,format=BGR,width=800,height=600 ! "
            "appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
        )
        bus = None
        self._clear_frames()
        self.first_frame_received = False
        self.attempt_started = time.monotonic()
        self.exit_reason = _RESULT_FAILURE

        try:
            self.pipeline = Gst.parse_launch(pipeline_str)
            source = self.pipeline.get_by_name("source")
            sink = self.pipeline.get_by_name("sink")
            source.set_property("location", self.create_url(self.channel))
            sink.connect("new-sample", self.on_new_sample)
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
                pipeline._put_status("change_cam", False)
                failed_attempts = 0
                continue

            shutdown_event.wait(PIPELINE_RETRY_DELAY_SECONDS)
    finally:
        pipeline._stop_snapshot_recording()
        pipeline._remove_sources()
        if pipeline.pipeline:
            pipeline.pipeline.set_state(Gst.State.NULL)
        cv.destroyAllWindows()
