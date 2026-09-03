"""Interactive marking of four undistorted barcode panels; no automatic clicks."""

import argparse
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox

import cv2 as cv
import numpy as np
from PIL import Image, ImageTk

from calibration.decoding.regions import ManualRegions, PANEL_NAMES, POINT_NAMES, middle_index
from calibration.paths import suggested_intrinsics
from calibration.analysis.recording import load_manifest, normalize_timing_row, load_epochs
from calibration.decoding.geometry import Undistorter

COLORS = ("#47d9ff", "#ffc857", "#b8ee69", "#ed9cff")


class RegionPicker(tk.Toplevel):
    def __init__(self, parent, folder, intrinsics, alpha=0.0, on_saved=None, on_cancel=None):
        super().__init__(parent)
        self.folder = Path(folder).resolve()
        self.intrinsics = intrinsics
        self.on_saved, self.on_cancel = on_saved, on_cancel
        self.points, self.point_frames = [], []
        self.photo = None
        self.scale = 1.
        self.size = None
        self.title("Mark barcode panels — " + self.folder.name)
        self.geometry(f"{min(1500, self.winfo_screenwidth()-60)}x{min(950, self.winfo_screenheight()-80)}")
        self.minsize(760, 520)
        epochs = load_epochs(self.folder)
        self.rows = [normalize_timing_row(row, epochs) for row in load_manifest(self.folder)]
        self.index = middle_index(len(self.rows))
        self.alpha = tk.StringVar(value=f"{alpha:g}")
        self.brighten = tk.BooleanVar(value=True)
        self.instruction = tk.StringVar()
        self.frame_label = tk.StringVar()
        self.position = tk.StringVar(value=str(self.index+1))
        outer = ttk.Frame(self, padding=8)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=self.folder.name, font=("TkDefaultFont", 12, "bold")).pack(anchor="w")
        ttk.Label(outer, text="Mark the white barcode panel, including its quiet margins. Exclude the time and underline.").pack(anchor="w")
        ttk.Label(outer, text="Panels: top-left → top-right → bottom-right → bottom-left. In each panel: TL → TR → BR → BL.").pack(anchor="w")
        ttk.Label(outer, textvariable=self.instruction, font=("TkDefaultFont", 11, "bold")).pack(anchor="w", pady=5)
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=3)
        ttk.Button(row, text="Previous frame", command=lambda: self.change_frame(self.index-1)).pack(side="left")
        ttk.Button(row, text="Next frame", command=lambda: self.change_frame(self.index+1)).pack(side="left", padx=3)
        ttk.Button(row, text="Random middle frame", command=lambda: self.change_frame(middle_index(len(self.rows)))).pack(side="left", padx=3)
        ttk.Label(row, text="Frame").pack(side="left", padx=(10, 2))
        entry = ttk.Entry(row, textvariable=self.position, width=7)
        entry.pack(side="left")
        entry.bind("<Return>", lambda _: self.go())
        ttk.Button(row, text="Go", command=self.go).pack(side="left", padx=3)
        ttk.Label(row, textvariable=self.frame_label).pack(side="left", padx=8)
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="Undistortion alpha").pack(side="left")
        self.alpha_box = ttk.Combobox(row, textvariable=self.alpha, values=("0", "0.25", "0.5", "0.75", "1"), width=6, state="readonly")
        self.alpha_box.pack(side="left", padx=4)
        self.alpha_box.bind("<<ComboboxSelected>>", lambda _: self.load_frame(fit=True))
        ttk.Checkbutton(row, text="Brighten for marking", variable=self.brighten, command=self.draw).pack(side="left", padx=6)
        ttk.Button(row, text="Fit image", command=self.fit).pack(side="left")
        ttk.Label(row, text="Wheel: zoom • Right click: undo • Points stay when changing frame").pack(side="left", padx=8)
        holder = ttk.Frame(outer)
        holder.pack(fill="both", expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(holder, background="#161a20", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        sx = ttk.Scrollbar(holder, orient="horizontal", command=self.canvas.xview)
        sy = ttk.Scrollbar(holder, orient="vertical", command=self.canvas.yview)
        sx.grid(row=1, column=0, sticky="ew")
        sy.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=sx.set, yscrollcommand=sy.set)
        self.canvas.bind("<Button-1>", self.click)
        self.canvas.bind("<Button-3>", lambda _: self.undo())
        self.canvas.bind("<MouseWheel>", lambda e: self.zoom(1.2 if e.delta > 0 else 1/1.2, e))
        self.canvas.bind("<Button-4>", lambda e: self.zoom(1.2, e))
        self.canvas.bind("<Button-5>", lambda e: self.zoom(1/1.2, e))
        row = ttk.Frame(outer)
        row.pack(fill="x", pady=(8, 0))
        ttk.Button(row, text="Undo corner", command=self.undo).pack(side="left")
        ttk.Button(row, text="Clear points", command=self.clear).pack(side="left", padx=5)
        self.save_button = ttk.Button(row, text="Save all 4 panels", command=self.save, state="disabled")
        self.save_button.pack(side="right")
        ttk.Button(row, text="Cancel", command=self.cancel).pack(side="right", padx=5)
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        self.bind("<Escape>", lambda _: self.cancel())
        self.after(100, lambda: self.load_frame(fit=True))

    def go(self):
        try:
            self.change_frame(int(self.position.get())-1)
        except ValueError:
            self.instruction.set("Enter a valid frame number")

    def change_frame(self, index):
        if not 0 <= index < len(self.rows):
            return
        self.index = index
        self.load_frame()

    def load_frame(self, fit=False):
        try:
            self.undistorter = Undistorter(self.intrinsics, alpha=float(self.alpha.get()))
            name = self.rows[self.index]["camera_frame"]
            path = (self.folder/name).resolve()
            if not path.is_relative_to(self.folder):
                raise ValueError("Image path is outside the recording")
            frame = cv.imread(str(path))
            if frame is None:
                raise ValueError("Could not read " + name)
            size = (frame.shape[1], frame.shape[0])
            if self.size and size != self.size:
                raise ValueError("The selected frame has a different resolution")
            self.size = size
            self.pixels = self.undistorter.image(frame)
            self.enhanced = cv.cvtColor(cv.createCLAHE(2, (8, 8)).apply(cv.cvtColor(self.pixels, cv.COLOR_BGR2GRAY)), cv.COLOR_GRAY2BGR)
            self.position.set(str(self.index+1))
            self.frame_label.set(f"/ {len(self.rows)} — undistorted")
            self.fit() if fit else self.draw()
        except (ValueError, OSError, cv.error) as error:
            self.instruction.set(str(error))

    def fit(self):
        if self.size:
            self.scale = min(self.canvas.winfo_width()/self.size[0], self.canvas.winfo_height()/self.size[1])
            self.draw()
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)

    def zoom(self, factor, event):
        if not self.size:
            return
        x, y = self.canvas.canvasx(event.x)/self.scale, self.canvas.canvasy(event.y)/self.scale
        self.scale = min(4., max(.2, self.scale*factor))
        self.draw()
        self.canvas.xview_moveto(max(0., (x*self.scale-event.x)/(self.size[0]*self.scale)))
        self.canvas.yview_moveto(max(0., (y*self.scale-event.y)/(self.size[1]*self.scale)))

    def click(self, event):
        if len(self.points) >= 16 or not self.size:
            return
        point = [self.canvas.canvasx(event.x)/self.scale, self.canvas.canvasy(event.y)/self.scale]
        if not (0 <= point[0] < self.size[0] and 0 <= point[1] < self.size[1]):
            return
        self.points.append(point)
        self.point_frames.append(self.rows[self.index]["camera_frame"])
        self.draw()

    def undo(self):
        if self.points:
            self.points.pop()
            self.point_frames.pop()
        self.draw()

    def clear(self):
        self.points.clear()
        self.point_frames.clear()
        self.draw()

    def draw(self):
        if not self.size:
            return
        pixels = self.enhanced if self.brighten.get() else self.pixels
        image = Image.fromarray(cv.cvtColor(pixels, cv.COLOR_BGR2RGB))
        image = image.resize((max(1, round(self.size[0]*self.scale)), max(1, round(self.size[1]*self.scale))), Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(image, master=self)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, image.width, image.height))
        for corner in range(4):
            points = np.array(self.points[corner*4:corner*4+4])*self.scale
            if len(points) > 1:
                if len(points) == 4:
                    self.canvas.create_polygon(points.ravel().tolist(), fill="", outline=COLORS[corner], width=2)
                else:
                    self.canvas.create_line(points.ravel().tolist(), fill=COLORS[corner], width=2)
            for j, (x, y) in enumerate(points):
                self.canvas.create_oval(x-4, y-4, x+4, y+4, fill=COLORS[corner], outline="black")
                self.canvas.create_text(x+8, y+8, text=f"{corner+1}.{j+1}", fill=COLORS[corner], anchor="nw")
        n = len(self.points)
        self.instruction.set(f"{n}/16 corners — {PANEL_NAMES[n//4]} panel: click {POINT_NAMES[n%4]}" if n < 16 else "16/16 corners — review the four panels, then save")
        self.save_button.configure(state="normal" if n == 16 else "disabled")
        self.alpha_box.configure(state="disabled" if n else "readonly")

    def save(self):
        try:
            regions = ManualRegions.save(self.folder, np.array(self.points).reshape(4, 4, 2), self.size,
                                         self.undistorter, self.point_frames)
        except (ValueError, OSError, cv.error) as error:
            messagebox.showerror("Check panel corners", str(error), parent=self)
            return
        print(f"Saved manual panels: {regions.path}", flush=True)
        self.destroy()
        if self.on_saved:
            self.on_saved(regions)

    def cancel(self):
        self.destroy()
        if self.on_cancel:
            self.on_cancel()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recordings", nargs="+", type=Path)
    parser.add_argument("--intrinsics", type=Path)
    parser.add_argument("--alpha", type=float, default=0.0)
    args = parser.parse_args()
    cv.setNumThreads(2)
    root = tk.Tk()
    root.withdraw()
    pending = iter(args.recordings)

    def next_recording(_regions=None):
        folder = next(pending, None)
        if folder is None:
            root.destroy()
            return
        intrinsics = args.intrinsics or suggested_intrinsics(folder)
        if not intrinsics:
            messagebox.showerror("Camera intrinsics", "Supply --intrinsics to mark an undistorted image", parent=root)
            root.destroy()
            return
        RegionPicker(root, folder, intrinsics, args.alpha, next_recording, root.destroy)

    next_recording()
    root.mainloop()


if __name__ == "__main__":
    main()
