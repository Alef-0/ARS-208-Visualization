import gi
import numpy as np
import cv2 as cv
import queue
import signal

gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GstApp, GLib

# Initialize GStreamer
Gst.init(None)
STOP = False

from multiprocessing import Pipe
from multiprocessing.connection import Connection

class GStreamerPipeline:
    def __init__(self, conn : Connection):
        self.pipeline = None
        self.main_loop = None
        self.running = False
        self.qu = queue.Queue(2)
        self.default_num = 2
        self.conn = conn
    
    def create_url(self, num):
        return f"rtsp://admin:l1v3user5@192.168.1.108:554/cam/realmonitor?channel={num}&subtype=0"
        
    def on_new_sample(self, sink):
        """Callback function called when a new sample is available from appsink"""
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        # Get buffer from sample
        buffer = sample.get_buffer()
        # Map the buffer to access the data
        success, map_info = buffer.map(Gst.MapFlags.READ)
        if not success:
            # sample.unref()
            return Gst.FlowReturn.ERROR
        
        # Get caps to determine video dimensions
        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        
        # Create numpy array from buffer data
        # Assuming BGR format with 3 channels (based on your caps filter)
        frame = np.frombuffer(map_info.data, dtype=np.uint8)
        frame = frame.reshape((height, width, 3))
        self.qu.put(frame)
        
        # Clean up
        buffer.unmap(map_info)
        # sample.unref()
        return Gst.FlowReturn.OK
    
    def on_pad_added(self, src, new_pad, depay): # Legado de c++!
        # Check if we can link this pad
        sink_pad = depay.get_static_pad("sink")
        if sink_pad.is_linked(): return

        # Attempt the link
        if new_pad.link(sink_pad) == Gst.PadLinkReturn.OK:
            print("Successfully linked rtspsrc to rtph264depay")
        else:
            print("Failed to link dynamic pad")

    def on_message(self, bus, message):
        """Handle bus messages"""
        msg_type = message.type
        if msg_type == Gst.MessageType.EOS:
            print("End of stream reached")
            self.main_loop.quit()
        elif msg_type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"Error: {err}, Debug: {debug}")
            self.main_loop.quit()
        elif msg_type == Gst.MessageType.WARNING:
            err, debug = message.parse_warning()
            print(f"Warning: {err}, Debug: {debug}")
    
    def cleanup(self):
        """Clean up resources"""
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        # cv.destroyAllWindows()

    def display_frame_in_gui_thread(self):
        # Check if a frame is available
        global STOP 
        if self.conn.poll():
            self.default_num = self.conn.recv()
            if self.main_loop: self.main_loop.quit()
            return GLib.SOURCE_REMOVE # Stop scheduling this function

        if self.qu:
            frame : np.ndarray = self.qu.get()
            frame = cv.resize(frame, (800, 600), interpolation=cv.INTER_LINEAR)
            cv.imshow("GStreamer Webcam Feed", frame)
            key = cv.waitKey(1) & 0xFF 

            if STOP:
                if self.main_loop: self.main_loop.quit()
                return GLib.SOURCE_REMOVE # Stop scheduling this function

            # if key == ord('q') or STOP:
            #     STOP = True
            #     if self.main_loop: self.main_loop.quit()
            #     return GLib.SOURCE_REMOVE # Stop scheduling this function
            # if key == ord('1'):
            #     if self.main_loop: self.main_loop.quit()
            #     self.default_num = 1
            #     return GLib.SOURCE_REMOVE # Stop scheduling this function
            # if key == ord('2'):
            #     if self.main_loop: self.main_loop.quit()
            #     self.default_num = 2
            #     return GLib.SOURCE_REMOVE # Stop scheduling this function
            # if key == ord('3'):
            #     if self.main_loop: self.main_loop.quit()
            #     self.default_num = 3
            #     return GLib.SOURCE_REMOVE # Stop scheduling this function
        return GLib.SOURCE_CONTINUE # Keep scheduling this function

    def run(self):
        """Main function to set up and run the pipeline"""
        # Your modified pipeline string
        pipeline_str = (
            "rtspsrc name=source latency=0 protocols=tcp+udp buffer-mode=1 do-retransmission=true ! "
            "rtph264depay name=rtph264depay ! h264parse ! avdec_h264 ! "
            "videoconvert ! capsfilter caps=\"video/x-raw,format=BGR\" ! "
            "appsink name=sink emit-signals=true sync=false"
        )
        
        # Create pipeline
        self.pipeline = Gst.parse_launch(pipeline_str)
        if not self.pipeline:
            print("Failed to create pipeline")
            return False
        
        # Get the appsink element
        appsink = self.pipeline.get_by_name("sink")
        if not appsink:
            print("Failed to get appsink element")
            return False
        source = self.pipeline.get_by_name("source")
        if not source: 
            print("SHIT WTF")
            return False
        depay = self.pipeline.get_by_name("rtph264depay")
        if not depay:
            print("Bitch WHY")
            return False

        # Connect the new-sample callback
        appsink.connect("new-sample", self.on_new_sample)
        # Connect to padding
        source.set_property("location", self.create_url(self.default_num))
        # source.connect("pad-added", self.on_pad_added, depay) # Não precisa para aqui aparentemente
        GLib.idle_add(self.display_frame_in_gui_thread)
        
        # Set up bus monitoring
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self.on_message)
        
        # Start pipeline
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("Unable to set pipeline to PLAYING state")
            return False
        
        # Create and run main loop
        self.running = True
        self.main_loop = GLib.MainLoop()

        self.main_loop.run()
        self.cleanup()
        return True
    
def signal_handler(sig, frame):
    print("Terminating Gstreamer Camera")
    global STOP
    STOP = True

def gstreamer_main(connection : Connection):
    signal.signal(signal.SIGTERM, signal_handler) 
    signal.signal(signal.SIGINT, signal_handler)
    pipeline = GStreamerPipeline(connection)
    global STOP

    while True:
        pipeline.run()
        if connection.poll() and connection.recv() == "STOP": STOP = True
        if STOP: pipeline.cleanup(); break

if __name__ == "__main__": 
    send, receive = Pipe()
    gstreamer_main(receive)