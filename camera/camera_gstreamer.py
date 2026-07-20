import queue
import signal
import socket

import cv2 as cv
import gi
import numpy as np

gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

Gst.init(None)


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

    @staticmethod
    def create_url(channel):
        return f"rtsp://admin:l1v3user5@192.168.1.108:554/cam/realmonitor?channel={channel}&subtype=0"

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
            try:
                self.frames.put_nowait(frame)
            except queue.Full:
                try:
                    self.frames.get_nowait()
                except queue.Empty:
                    pass
                self.frames.put_nowait(frame)
        finally:
            buffer.unmap(map_info)
        return Gst.FlowReturn.OK

    def on_message(self, _bus, message):
        if message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
            if message.type == Gst.MessageType.ERROR:
                error, debug = message.parse_error()
                print(f"GStreamer error: {error}; {debug}")
            if self.main_loop:
                self.main_loop.quit()

    def process_commands(self):
        restart = False
        while self.communicate.poll():
            event, value = self.communicate.recv()
            if event == "STOP":
                self.shutdown_event.set()
            elif event == "choose" and value != self.channel:
                self.channel = value
                restart = True
            elif event == "conn_cam":
                if self.connected:
                    self.connected = False
                    self.pool.put(("change_cam", False))
                    restart = True
                else:
                    try:
                        with socket.create_connection(("192.168.1.108", 554), timeout=2):
                            self.connected = True
                            self.pool.put(("change_cam", True))
                    except OSError:
                        self.pool.put(("change_cam", False))
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

    def run(self):
        pipeline_str = (
            "rtspsrc name=source latency=100 protocols=tcp ! "
            "rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! videoscale ! "
            "video/x-raw,format=BGR,width=800,height=600 ! "
            "appsink name=sink emit-signals=true sync=false max-buffers=1 drop=true"
        )
        self.pipeline = Gst.parse_launch(pipeline_str)
        source = self.pipeline.get_by_name("source")
        sink = self.pipeline.get_by_name("sink")
        source.set_property("location", self.create_url(self.channel))
        sink.connect("new-sample", self.on_new_sample)
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)
        self.main_loop = GLib.MainLoop()
        GLib.timeout_add(10, self.process_commands)
        GLib.timeout_add(10, self.display_latest_frame)
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.connected = False
            self.pool.put(("change_cam", False))
            return
        try:
            self.main_loop.run()
        finally:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
            self.main_loop = None


def gstreamer_main(connection, pool, shutdown_event):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, lambda *_: shutdown_event.set())
    pipeline = GStreamerPipeline(connection, pool, shutdown_event)
    try:
        while not shutdown_event.is_set():
            pipeline.process_commands()
            if pipeline.connected:
                pipeline.run()
            else:
                shutdown_event.wait(0.05)
    finally:
        if pipeline.pipeline:
            pipeline.pipeline.set_state(Gst.State.NULL)
        cv.destroyAllWindows()
