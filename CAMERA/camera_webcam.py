import gi
import sys
import queue
# Set GStreamer version requirement
gi.require_version('Gst', '1.0')
gi.require_version('GstApp', '1.0')
from gi.repository import Gst, GLib, GstApp

import cv2 as cv
import numpy as np
import threading as thr
import time

STOP = False

class Camera_exhibit:
    frame_queue = queue.Queue(maxsize=2)
    playing = True

    def __init__(self, args):
        Gst.init(args)
        self.called_once = False
        
        # Creating pipeline
        self.pipeline_str = (
            "autovideosrc ! "                      # Automatically selects a video source (webcam)
            "videoconvert ! "                      # Convert to the desired format
            "video/x-raw,format=RGB,width=640,height=480,framerate=30/1 ! " # Caps: enforce format and resolution
            "appsink name=mysink emit-signals=True" # Sink element for callbacks
        )
        self.pipeline = Gst.parse_launch(self.pipeline_str)
        if not self.pipeline: print("ERROR: Could not create pipeline. Check if your GStreamer installation is complete."); return
        
        # Get the appsink elements
        appsink = self.pipeline.get_by_name("mysink")
        if not appsink:
            print("ERROR: Could not get 'mysink' element. Check the pipeline string.")
            self.pipeline.set_state(Gst.State.NULL)
            return
        appsink.connect("new-sample", self.on_new_sample)

        # Start the pipeline (set state to PLAYING)
        print("Setting pipeline state to PLAYING...")
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            print("ERROR: Unable to set the pipeline to the playing state.")
            self.pipeline.set_state(Gst.State.NULL)
            return
        self.main_loop = GLib.MainLoop()
        
        GLib.idle_add(self.display_frame_in_gui_thread)
        GLib.timeout_add(1000, self.end)

        print("Webcam pipeline running. Press Ctrl+C to stop.")
        self.main_loop.run()
        self.end()

    def on_new_sample(self, appsink):
        sample = appsink.emit("pull-sample")
        if sample:
            buffer = sample.get_buffer()
            caps = sample.get_caps()
            struct = caps.get_structure(0)
            
            # Get dimensions and map data
            success, width = struct.get_int("width")
            success, height = struct.get_int("height")
            success, map_info = buffer.map(Gst.MapFlags.READ)
            
            if success:
                data = map_info.data
                
                # **CRITICAL CHANGE:** Convert Gst buffer to a NumPy array here.
                # We must use a copy, NOT a view, as the map_info will be unmapped.
                np_data = np.frombuffer(data, dtype=np.uint8).copy() 
                frame_np = np_data.reshape((height, width, 3))
                
                # Unmap immediately after copying the data
                buffer.unmap(map_info)
                
                # Safely queue the frame for the main thread
                # This is a simple (non-thread-safe) list for demonstration.
                # For production code, use a proper queue or lock.
                self.frame_queue.put(frame_np)
                
            return Gst.FlowReturn.OK
        return Gst.FlowReturn.ERROR
    
    def display_frame_in_gui_thread(self):
        # Check if a frame is available
        if self.frame_queue:
            frame_np = self.frame_queue.get()
            # Now we perform the GUI/OpenCV operations safely
            frame_bgr = cv.cvtColor(frame_np, cv.COLOR_RGB2BGR)
            cv.imshow("GStreamer Webcam Feed", frame_bgr)
            # This is where the problematic killTimer call originates.
            # It is now safe in the main thread.
            key = cv.waitKey(1) & 0xFF 
            global STOP
            if key == ord('q') or STOP:
                # Stop the main loop and close OpenCV
                self.playing = False
                # cv.destroyWindow("GStreamer Webcam Feed") 
                if self.main_loop: self.main_loop.quit()
                # Can't call destroy all windows before quitting the main_loop
                # Because something bugs out
                # self.main_loop.quit()
                cv.destroyAllWindows()
                return GLib.SOURCE_REMOVE # Stop scheduling this function
        return GLib.SOURCE_CONTINUE # Keep scheduling this function
    
    def end(self):
        # global STOP
        if not STOP: print("CAMERA_WORKING"); return True;
        # if self.main_loop: self.main_loop.quit()
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
            # print("Pipeline stopped and resources released.")
            # self.main_loop.quit()
        # print("OUT")
        # cv.destroyAllWindows()
        return False

import signal

def signal_handler(sig, frame):
    print("Terminated")
    global STOP
    STOP = True

def camera_start():
    signal.signal(signal.SIGTERM, signal_handler) 
    signal.signal(signal.SIGINT, signal_handler)
    print("STARTING")
    cam = Camera_exhibit(sys.argv)
    print("ENDED")

from multiprocessing import Process

if __name__ == "__main__":
    camera_start()
    # try:
    #     time.sleep(30)
    # except KeyboardInterrupt: 
    #     pass
    # p1.terminate()
