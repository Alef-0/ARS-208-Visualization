"""Background decoding and folder scans, independent of Tk widgets."""

from queue import Empty, Queue
import threading

import cv2 as cv
import numpy as np

from .calibration_data import CalibrationRecording, summarize_predictions


class InspectionWorker(threading.Thread):
    """Serial decoder ownership, with folder scans yielding to navigation."""

    def __init__(self):
        super().__init__(daemon=True, name="calibration-inspection")
        self.requests, self.results = Queue(), Queue()
        self.closed = threading.Event()
        self.model = None
        self.session = 0
        self.scan = None
        self.latest_frame = None

    def submit(self, session, action, **options):
        if action == "frame":
            self.latest_frame = (session, options["request"])
        self.requests.put((session, action, options))

    def stop(self):
        self.closed.set()

    def run(self):
        cv.setNumThreads(2)
        try:
            while not self.closed.is_set():
                try:
                    session, action, options = self.requests.get(timeout=.02)
                except Empty:
                    if self.scan is not None:
                        self.scan_step()
                    continue
                try:
                    if action == "load":
                        self.scan = None
                        self.session = session
                        if self.model:
                            self.model.close()
                        self.model = None
                        self.model = CalibrationRecording(options["folder"], options.get("intrinsics"))
                        self.results.put((session, "loaded", {
                            "count": len(self.model.rows), "folder": str(self.model.folder),
                            "intrinsics": bool(self.model.undistorter), "session": self.model.session,
                            "intrinsics_assumed_size": bool(self.model.undistorter and not self.model.undistorter.calibration_size),
                        }))
                    elif session == self.session and self.model:
                        if action == "frame":
                            if self.latest_frame == (session, options["request"]):
                                self.frame(options)
                        elif action == "scan":
                            self.scan = {"index": 0, "results": [], "compare": options["compare"],
                                         "errors": [], "request": options["request"]}
                        elif action == "cancel":
                            self.finish_scan("Cancelled")
                except Exception as error:
                    self.results.put((session, "error", {"message": str(error), "action": action,
                                                         "request": options.get("request")}))
        finally:
            if self.model:
                self.model.close()

    def frame(self, options):
        result = self.model.inspect(options["index"], options["compare"])
        variant = options["variant"]
        pixels = self.model.image(options["index"], variant)
        locations = {}
        for observation in result["observations"]:
            points = observation.get("points", [])
            if self.model.undistorter:
                points = self.model.undistorter.points(points, observation["variant"], variant, result["size"])
            locations[observation["id"]] = np.asarray(points).reshape(-1, 2).tolist()
        self.results.put((self.session, "frame", {"request": options["request"], "result": result,
                                                  "pixels": pixels, "locations": locations, "variant": variant}))

    def scan_step(self):
        scan = self.scan
        try:
            result = self.model.inspect(scan["index"], scan["compare"])
            scan["results"].append({"prediction": result["prediction"], "timing": result["timing"]})
        except Exception as error:
            scan["errors"].append(f"Frame {scan['index']+1}: {error}")
        scan["index"] += 1
        if scan["index"] % 20 == 0:
            self.results.put((self.session, "progress", {"count": scan["index"], "total": len(self.model.rows),
                                                         "request": scan["request"]}))
        if scan["index"] >= len(self.model.rows):
            self.finish_scan("Complete")

    def finish_scan(self, state):
        if self.scan is None:
            return
        self.results.put((self.session, "summary", {"state": state,
                          "summary": summarize_predictions(self.scan["results"]),
                          "errors": self.scan["errors"], "compare": self.scan["compare"],
                          "request": self.scan["request"]}))
        self.scan = None
