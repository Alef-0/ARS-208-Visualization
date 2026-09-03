"""Standalone calibration viewer: python3 -m processing.visualization.calibration_viewer."""

import argparse
from datetime import datetime
from pathlib import Path
from queue import Empty
import tkinter as tk
from tkinter import filedialog, ttk

import cv2 as cv
import numpy as np
from PIL import Image, ImageTk

from CALIBRATION.paths import PROJECT_ROOT as ROOT, suggested_intrinsics
from .calibration_worker import InspectionWorker

COLORS = ("#47d9ff", "#ffc857", "#b8ee69", "#ed9cff")


def seconds_ns(value):
    if value is None:
        return "unavailable"
    sign = "-" if value < 0 else ""
    seconds, nanos = divmod(abs(int(value)), 1_000_000_000)
    return f"{sign}{seconds}.{nanos:09d} s"


def wall_time(value):
    if value is None:
        return "unavailable"
    try:
        return datetime.fromtimestamp(value / 1e9).astimezone().isoformat(timespec="microseconds")
    except (ValueError, OverflowError, OSError):
        return f"{value} ns (outside calendar range)"


class CalibrationViewer:
    def __init__(self, root, folder=None, intrinsics=None, undistorted=False):
        self.root = root
        root.title("Calibration Visualization")
        width, height = min(1480, root.winfo_screenwidth()-60), min(1000, root.winfo_screenheight()-90)
        root.geometry(f"{max(800, width)}x{max(650, height)}")
        root.minsize(800, 650)
        self.worker = InspectionWorker()
        self.worker.start()
        self.session, self.request, self.count, self.index = 0, 0, 0, 0
        self.pending_index = 0
        self.current = None
        self.scanning = False
        self.scan_request = 0
        self.photo = None
        self.resize_job = None
        self.folder = tk.StringVar(value=str(folder or ROOT/"recordings"))
        self.intrinsics = tk.StringVar(value=str(intrinsics or suggested_intrinsics(folder)))
        self.variant = tk.StringVar(value="Undistorted" if undistorted else "Original")
        self.compare = tk.BooleanVar(value=undistorted)
        self.boxes = tk.BooleanVar(value=True)
        self.all_boxes = tk.BooleanVar(value=False)
        self.method = tk.StringVar(value="All methods")
        self.position = tk.StringVar(value="1")
        self.status = tk.StringVar(value="Choose a calibration recording folder.")
        self.frame_title = tk.StringVar(value="No recording loaded")
        self.prediction = tk.StringVar(value="Best candidate offset: unavailable")
        self.timing = tk.StringVar(value="")
        self.folder_summary = tk.StringVar(value="Folder estimate: select Analyze folder to measure every frame.")
        self.build()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Left>", lambda event: self.keyboard_step(event, -1))
        root.bind("<Right>", lambda event: self.keyboard_step(event, 1))
        self.root.after(50, self.poll)
        if folder:
            self.root.after(100, self.load)

    def build(self):
        outer = ttk.Frame(self.root, padding=8)
        outer.pack(fill="both", expand=True)
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Label(row, text="Recording folder").pack(side="left")
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse…", command=self.browse_folder).pack(side="left")
        ttk.Button(row, text="Open folder", command=self.load).pack(side="left", padx=4)
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(5, 7))
        ttk.Label(row, text="Camera intrinsics").pack(side="left")
        ttk.Entry(row, textvariable=self.intrinsics).pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="Browse…", command=self.browse_intrinsics).pack(side="left")
        ttk.Button(row, text="Apply intrinsics", command=lambda: self.load(preserve=True)).pack(side="left", padx=4)
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Button(row, text="Previous", command=lambda: self.step(-1)).pack(side="left")
        ttk.Button(row, text="Next", command=lambda: self.step(1)).pack(side="left", padx=4)
        entry = ttk.Entry(row, textvariable=self.position, width=7)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _: self.go())
        ttk.Button(row, text="Go", command=self.go).pack(side="left", padx=4)
        self.slider = ttk.Scale(row, from_=1, to=1)
        self.slider.pack(side="left", fill="x", expand=True, padx=5)
        self.slider.bind("<ButtonRelease-1>", lambda _: self.go(round(self.slider.get())))
        self.scan_button = ttk.Button(row, text="Analyze folder", command=self.scan)
        self.scan_button.pack(side="left", padx=4)
        ttk.Button(row, text="Cancel analysis", command=self.cancel_scan).pack(side="left")
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=6)
        ttk.Label(row, text="Image").pack(side="left")
        combo = ttk.Combobox(row, textvariable=self.variant, values=("Original", "Undistorted"), state="readonly", width=13)
        combo.pack(side="left", padx=4)
        combo.bind("<<ComboboxSelected>>", lambda _: self.show_frame())
        ttk.Checkbutton(row, text="Also decode undistorted", variable=self.compare,
                        command=self.comparison_changed).pack(side="left", padx=8)
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(0, 4))
        ttk.Checkbutton(row, text="Mark source region", variable=self.boxes, command=self.draw).pack(side="left")
        ttk.Checkbutton(row, text="Show all codes", variable=self.all_boxes, command=self.draw).pack(side="left", padx=8)
        combo = ttk.Combobox(row, textvariable=self.method, state="readonly", width=18,
                             values=("All methods", "OpenCV", "ZBar", "Outline scanlines"))
        combo.pack(side="left")
        combo.bind("<<ComboboxSelected>>", lambda _: self.populate())
        ttk.Label(outer, textvariable=self.frame_title, font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        prediction_label = ttk.Label(outer, textvariable=self.prediction, wraplength=1200)
        prediction_label.pack(anchor="w", pady=3)
        timing_label = ttk.Label(outer, textvariable=self.timing, wraplength=1200)
        timing_label.pack(anchor="w")
        panes = ttk.Panedwindow(outer, orient="vertical")
        panes.pack(fill="both", expand=True, pady=5)
        self.canvas = tk.Canvas(panes, background="#15191e", highlightthickness=0, height=420)
        panes.add(self.canvas, weight=3)
        self.canvas.bind("<Configure>", self.schedule_draw)
        lower = ttk.Frame(panes)
        panes.add(lower, weight=2)
        columns = ("use", "decoder", "image", "quadrant", "code", "time", "offset", "status")
        self.table = ttk.Treeview(lower, columns=columns, show="headings", height=7, selectmode="browse")
        headings = ("Use", "Decoder", "Decoded image", "Display quadrant", "EAN-13", "Screen time (s)", "PTS − screen (ms)", "Evidence")
        widths = (45, 125, 100, 110, 135, 150, 120, 230)
        for key, heading, width in zip(columns, headings, widths):
            self.table.heading(key, text=heading)
            self.table.column(key, width=width, minwidth=45, stretch=key == "status")
        self.table.grid(row=0, column=0, sticky="nsew")
        vertical = ttk.Scrollbar(lower, orient="vertical", command=self.table.yview)
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal = ttk.Scrollbar(lower, orient="horizontal", command=self.table.xview)
        horizontal.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        lower.columnconfigure(0, weight=1)
        lower.rowconfigure(0, weight=1)
        self.table.tag_configure("best", background="#dcefdc", foreground="#102010")
        self.table.tag_configure("rejected", foreground="#98502d")
        self.table.bind("<<TreeviewSelect>>", lambda _: self.selection_changed())
        self.details = tk.Text(lower, height=5, wrap="word", font=("TkFixedFont", 9), state="disabled")
        self.details.grid(row=2, column=0, sticky="ew", pady=(4, 0))
        details_scroll = ttk.Scrollbar(lower, orient="vertical", command=self.details.yview)
        details_scroll.grid(row=2, column=1, sticky="ns")
        self.details.configure(yscrollcommand=details_scroll.set)
        summary_label = ttk.Label(outer, textvariable=self.folder_summary, wraplength=1200)
        summary_label.pack(anchor="w", pady=3)
        status_label = ttk.Label(outer, textvariable=self.status, wraplength=1200)
        status_label.pack(anchor="w")
        legend_label = ttk.Label(outer, text="Quadrants come from the display journal. Boxes show detector regions; they do not locate the physical exposure instant.",
                                 wraplength=1200)
        legend_label.pack(anchor="w", pady=(3, 0))
        outer.bind("<Configure>", lambda event: [label.configure(wraplength=max(300, event.width-20))
                   for label in (prediction_label, timing_label, summary_label, status_label, legend_label)])

    def browse_folder(self):
        folder = filedialog.askdirectory(parent=self.root, initialdir=self.folder.get())
        if folder:
            self.folder.set(folder)
            self.load()

    def browse_intrinsics(self):
        path = filedialog.askopenfilename(parent=self.root, title="Camera intrinsic coefficients",
                                         filetypes=(("JSON", "*.json"), ("All files", "*")))
        if path:
            self.intrinsics.set(path)
            if self.count:
                self.load(preserve=True)

    def load(self, preserve=False):
        self.pending_index = self.index if preserve else 0
        self.session += 1
        self.count = 0
        self.current = None
        self.scanning = False
        self.scan_button.configure(state="normal")
        self.folder_summary.set("Folder estimate: not analyzed for this folder and decoder mode.")
        self.clear_frame()
        self.status.set("Loading recording journals…")
        self.worker.submit(self.session, "load", folder=self.folder.get().strip(),
                           intrinsics=self.intrinsics.get().strip() or None)

    def keyboard_step(self, event, delta):
        if event.widget.winfo_class() not in ("Entry", "TEntry", "Text", "TCombobox"):
            self.step(delta)
            return "break"

    def step(self, delta):
        if self.count:
            self.go(self.index+1+delta)

    def go(self, number=None):
        if not self.count:
            return
        try:
            index = int(self.position.get() if number is None else number)-1
            if not 0 <= index < self.count:
                raise ValueError()
        except ValueError:
            self.status.set(f"Choose a frame number from 1 to {self.count}.")
            return
        self.index = index
        self.show_frame()

    def comparison_changed(self):
        self.cancel_scan()
        self.folder_summary.set("Decoder mode changed; analyze the folder again for a matching estimate.")
        self.show_frame()

    def clear_frame(self):
        self.canvas.delete("all")
        self.table.delete(*self.table.get_children())
        self.set_details("")
        self.prediction.set("Best candidate offset: awaiting decoding")
        self.timing.set("")

    def show_frame(self):
        if not self.count:
            return
        if (self.variant.get() == "Undistorted" or self.compare.get()) and not self.loaded["intrinsics"]:
            self.variant.set("Original")
            self.compare.set(False)
            self.status.set("No intrinsics loaded; showing the original image.")
        self.request += 1
        self.current = None
        self.position.set(str(self.index+1))
        self.slider.set(self.index+1)
        self.clear_frame()
        self.frame_title.set(f"Frame {self.index+1} / {self.count} — decoding…")
        self.status.set("Reading recorded pixels; originals are preserved.")
        self.worker.submit(self.session, "frame", index=self.index, compare=self.compare.get(),
                           variant=self.variant.get(), request=self.request)

    def scan(self):
        if not self.count:
            return
        if self.compare.get() and not self.loaded["intrinsics"]:
            self.status.set("Apply camera intrinsics before decoding undistorted images.")
            return
        self.scanning = True
        self.scan_request += 1
        self.scan_button.configure(state="disabled")
        self.folder_summary.set("Analyzing all frames… Navigation remains available.")
        self.worker.submit(self.session, "scan", compare=self.compare.get(), request=self.scan_request)

    def cancel_scan(self):
        if self.scanning:
            self.worker.submit(self.session, "cancel")
        self.scanning = False
        self.scan_button.configure(state="normal")

    def poll(self):
        try:
            while True:
                session, kind, payload = self.worker.results.get_nowait()
                if session != self.session:
                    continue
                if kind == "loaded":
                    self.loaded = payload
                    self.count = payload["count"]
                    self.index = min(self.pending_index, self.count-1)
                    self.slider.configure(to=self.count)
                    self.show_frame()
                elif kind == "frame" and payload["request"] == self.request:
                    self.current = payload
                    self.populate()
                elif kind == "error":
                    if payload["request"] is None or payload["request"] == self.request:
                        self.status.set(payload["message"])
                        if payload["action"] == "load":
                            self.frame_title.set("Recording could not be loaded")
                elif kind == "progress" and self.scanning and payload["request"] == self.scan_request:
                    self.folder_summary.set(f"Analyzed {payload['count']} / {payload['total']} frames…")
                elif kind == "summary" and payload["request"] == self.scan_request:
                    self.scanning = False
                    self.scan_button.configure(state="normal")
                    if payload["compare"] == self.compare.get():
                        self.show_summary(payload)
        except Empty:
            pass
        self.root.after(50, self.poll)

    def show_summary(self, payload):
        report = payload["summary"]
        if report["multiple_epochs"]:
            parts = []
            for epoch, data in report["by_epoch"].items():
                metrics = []
                for key, label in (("outline", "outline supported"), ("provisional", "provisional")):
                    metric = data[key]
                    if metric:
                        metrics.append(f"{label} {metric['median']:.2f} ms ({metric['count']} frames)")
                parts.append(f"epoch {epoch}: " + (", ".join(metrics) or "no usable estimate"))
            text = "Separate stream estimates (no combined correction): " + "; ".join(parts)
        else:
            parts = []
            for key, label in (("outline", "Outline supported"), ("provisional", "Provisional newest-code")):
                metric = report[key]
                if metric:
                    parts.append(f"{label}: median {metric['median']:.2f} ms; 90% range {metric['p05']:.2f}–{metric['p95']:.2f} ms; n={metric['count']}")
            text = " | ".join(parts) or "No usable offset estimate"
        excluded = sum(report["excluded"].values())
        self.folder_summary.set(f"{payload['state']}: {report['frames']} frames. {text}. Excluded: {excluded}; read errors: {len(payload['errors'])}.")

    def populate(self):
        if not self.current:
            return
        result = self.current["result"]
        prediction, timing = result["prediction"], result["timing"]
        self.frame_title.set(f"{result['filename']} — {result['index']+1} / {self.count} — {self.current['variant']}")
        offset = prediction["offset_ms"]
        value = "unavailable" if offset is None else f"{offset:.3f} ms (subtract from host-anchored PTS)"
        self.prediction.set(f"Best candidate offset: {value} | {prediction['status']} | {prediction['quadrant']} | {prediction['basis']}\n"
                            f"Selected screen reference: {seconds_ns(prediction['marker_ns'])}")
        self.timing.set(f"PTS: {seconds_ns(timing['pts_ns'])} | Frame monotonic: {seconds_ns(timing['frame_monotonic_ns'])} | Epoch: {timing['stream_epoch']}\n"
                        f"Host media: {wall_time(timing['media_unix_ns'])} | Receipt: {wall_time(timing['received_unix_ns'])} | DVR NTP: {wall_time(timing['reference_ntp_ns'])}")
        self.table.delete(*self.table.get_children())
        shown = []
        for observation in result["observations"]:
            if self.method.get() not in ("All methods", observation["method"]):
                continue
            best = observation["id"] in prediction["source_ids"]
            marker = observation["marker_ns"]
            stamp = seconds_ns(marker).removesuffix(" s") if marker is not None else (
                f"{observation['payload_ms']/1000:.3f} (unmatched)" if observation['payload_ms'] is not None else "—")
            self.table.insert("", "end", iid=observation["id"], values=(
                "Best" if best else "", observation["method"], observation["variant"], observation["quadrant"],
                observation["code"] or observation["raw_code"], stamp,
                f"{observation['offset_ms']:.3f}" if observation["offset_ms"] is not None else "—", observation["status"]),
                tags=("best",) if best else (() if observation["valid"] else ("rejected",)))
            shown.append(observation)
        initial = next((o for o in shown if o["id"] in prediction["source_ids"] and o["variant"] == self.current["variant"]),
                       next((o for o in shown if o["id"] in prediction["source_ids"]), shown[0] if shown else None))
        if initial:
            self.table.selection_set(initial["id"])
            self.table.see(initial["id"])
        self.selection_changed()
        self.status.set("; ".join(prediction["warnings"]) or "Select a decoded row to inspect its source region.")
        if self.loaded["intrinsics_assumed_size"]:
            self.status.set(self.status.get()+" | Intrinsics have no image_size; assuming the recorded image dimensions.")

    def set_details(self, text):
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def selection_changed(self):
        if not self.current:
            return
        result = self.current["result"]
        selected = self.table.selection()
        observation = next((o for o in result["observations"] if selected and o["id"] == selected[0]), None)
        text = "\n".join(result["methods"])
        if observation:
            points = np.asarray(observation.get("points", [])).reshape(-1, 2)
            bounds = (f"x={points[:, 0].min():.1f}..{points[:, 0].max():.1f}, "
                      f"y={points[:, 1].min():.1f}..{points[:, 1].max():.1f}") if len(points) else "unavailable"
            display = observation["display_timing"]
            issues = "; ".join(display["issue_codes"]) if display else "No display entry"
            text = (f"Selected #{observation['id']}: {observation['method']} / {observation['variant']} | "
                    f"Display quadrant: {observation['quadrant']} | Display index: {observation['display_index']}\n"
                    f"EAN payload: {observation['payload_ms']} monotonic ms | Exact journal marker: {seconds_ns(observation['marker_ns'])}\n"
                    f"Source pixels ({observation['variant']} coordinates): {bounds}. Overlay mapped to {self.current['variant']}.\n"
                    f"Timing issues: {issues or 'none reported'}\n" + text)
        recorded = self.loaded["session"].get("image_adjustment_ns")
        if recorded is not None:
            text += f"\nRecorded correction: {recorded/1e6:.3f} ms; stored exposure estimate: {wall_time(result['raw_timing'].get('estimated_exposure_unix_ns'))}"
        self.set_details(text)
        self.draw()

    def schedule_draw(self, _event=None):
        if self.resize_job:
            self.root.after_cancel(self.resize_job)
        self.resize_job = self.root.after(80, self.draw)

    def draw(self):
        self.resize_job = None
        self.canvas.delete("all")
        if not self.current:
            return
        pixels = self.current["pixels"]
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        scale = min(width/pixels.shape[1], height/pixels.shape[0])
        size = max(1, int(pixels.shape[1]*scale)), max(1, int(pixels.shape[0]*scale))
        image = Image.fromarray(cv.cvtColor(pixels, cv.COLOR_BGR2RGB)).resize(size, Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(image)
        left, top = (width-size[0])/2, (height-size[1])/2
        self.canvas.create_image(left, top, image=self.photo, anchor="nw")
        if not self.boxes.get():
            return
        selection = self.table.selection()
        drawn = set()
        rows = [o for o in self.current["result"]["observations"] if o["id"] in self.table.get_children()]
        rows.sort(key=lambda o: (not bool(selection and o["id"] == selection[0]), o["variant"] != self.current["variant"]))
        for observation in rows:
            selected = bool(selection and observation["id"] == selection[0])
            if not selected and (not self.all_boxes.get() or observation["code"] in drawn):
                continue
            points = np.asarray(self.current["locations"][observation["id"]]).reshape(-1, 2)
            if len(points) < 2 or not np.isfinite(points).all():
                continue
            points = points*scale + np.array([left, top])
            color = COLORS[observation["corner"]] if observation["corner"] is not None else "#ff755c"
            self.canvas.create_polygon(points.ravel().tolist(), fill="", outline=color, width=3 if selected else 1)
            x, y = max(3, min(width-230, points[:, 0].min())), max(3, min(height-40, points[:, 1].min()-34))
            stamp = f"{observation['payload_ms']/1000:.3f} s" if observation["payload_ms"] is not None else observation["raw_code"]
            label = self.canvas.create_text(x+4, y+3, text=f"#{observation['id']} {observation['quadrant']} · {stamp}",
                                            fill=color, anchor="nw", font=("TkDefaultFont", 10, "bold"))
            background = self.canvas.create_rectangle(self.canvas.bbox(label), fill="#15191e", outline=color)
            self.canvas.tag_raise(label, background)
            drawn.add(observation["code"])

    def close(self):
        self.worker.stop()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser(description="Inspect calibration images, decoded timestamps, and candidate offsets")
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("--intrinsics", type=Path)
    parser.add_argument("--undistorted", action="store_true")
    args = parser.parse_args()
    root = tk.Tk()
    CalibrationViewer(root, args.folder, args.intrinsics, args.undistorted)
    root.mainloop()


if __name__ == "__main__":
    main()
