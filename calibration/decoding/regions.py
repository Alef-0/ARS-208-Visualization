"""Recording-bound manual barcode panels in undistorted image coordinates."""

import hashlib
import json
from pathlib import Path
import random

import cv2 as cv
import numpy as np

REGIONS_NAME = "calibration_regions.json"
PANEL_NAMES = ("Top-left", "Top-right", "Bottom-right", "Bottom-left")
POINT_NAMES = ("top-left", "top-right", "bottom-right", "bottom-left")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def middle_index(count, rng=None):
    if count < 1:
        raise ValueError("The recording contains no camera frames")
    return (rng or random).randrange(int((count-1)*.4), int((count-1)*.6)+1)


def validate_quads(quads, size):
    points = np.asarray(quads, dtype=np.float32)
    if points.shape != (4, 4, 2) or not np.isfinite(points).all():
        raise ValueError("Mark four corners for each of the four panels")
    width, height = size
    if np.any(points < 0) or np.any(points[:, :, 0] >= width) or np.any(points[:, :, 1] >= height):
        raise ValueError("All corners must lie inside the undistorted image")
    for name, quad in zip(PANEL_NAMES, points):
        if (not cv.isContourConvex(quad) or cv.contourArea(quad, oriented=True) < 64
                or (quad[0, 1]+quad[1, 1]) >= (quad[2, 1]+quad[3, 1])
                or (quad[0, 0]+quad[3, 0]) >= (quad[1, 0]+quad[2, 0])):
            raise ValueError(f"{name}: click top-left, top-right, bottom-right, bottom-left")
    for i in range(4):
        for j in range(i):
            if cv.intersectConvexConvex(points[i], points[j])[0] > 4:
                raise ValueError("The barcode panels must not overlap")
    return points


class ManualRegions:
    def __init__(self, data, path):
        self.data, self.path = data, Path(path)
        if data.get("format") != "segcom-manual-panels-v1" or data.get("space") != "Undistorted":
            raise ValueError("Unsupported manual panel file")
        self.size = tuple(data["image_size"])
        if len(self.size) != 2 or any(type(v) is not int or v <= 0 for v in self.size):
            raise ValueError("Invalid manual panel image size")
        self.quads = validate_quads(data["panels_tl_tr_br_bl"], self.size)
        self.alpha = float(data["alpha"])
        if not np.isfinite(self.alpha) or not 0 <= self.alpha <= 1:
            raise ValueError("Invalid manual panel alpha")
        self.output_matrix = np.asarray(data["output_matrix"], np.float64)
        if self.output_matrix.shape != (3, 3) or not np.isfinite(self.output_matrix).all() or abs(np.linalg.det(self.output_matrix)) < 1e-8:
            raise ValueError("Invalid saved undistortion matrix")

    @classmethod
    def load(cls, folder, intrinsics=None, required=False):
        folder = Path(folder).resolve()
        path = folder / REGIONS_NAME
        if not path.is_file():
            if required:
                raise FileNotFoundError("Mark the four barcode panels before analysis")
            return None
        result = cls(json.loads(path.read_text(encoding="utf-8")), path)
        for name in ("camera_timestamps.jsonl", "display_timestamps.jsonl"):
            if digest(folder/name) != result.data["journals_sha256"].get(name):
                raise ValueError("Saved panels belong to different recording evidence; mark them again")
        for name, checksum in result.data["reference_images_sha256"].items():
            image = (folder/name).resolve()
            if not image.is_relative_to(folder) or digest(image) != checksum:
                raise ValueError("A panel-selection reference image has changed")
        intrinsic_path = Path(intrinsics or result.data["intrinsics_path"]).expanduser().resolve()
        if digest(intrinsic_path) != result.data["intrinsics_sha256"]:
            raise ValueError("Intrinsic coefficients changed; mark the panels again")
        result.intrinsics_path = intrinsic_path
        return result

    @classmethod
    def save(cls, folder, quads, size, undistorter, point_frames):
        folder = Path(folder).resolve()
        points = validate_quads(quads, size)
        if len(point_frames) != 16:
            raise ValueError("Each selected corner must name its reference frame")
        references = {}
        for name in point_frames:
            image = (folder/name).resolve()
            if not image.is_relative_to(folder):
                raise ValueError("Selection image is outside the recording")
            references[name] = digest(image)
        data = {"format": "segcom-manual-panels-v1", "space": "Undistorted",
                "image_size": list(size), "alpha": undistorter.alpha,
                "output_matrix": undistorter.geometry(size)[1].tolist(),
                "intrinsics_path": str(undistorter.path), "intrinsics_sha256": digest(undistorter.path),
                "journals_sha256": {name: digest(folder/name) for name in
                                    ("camera_timestamps.jsonl", "display_timestamps.jsonl")},
                "reference_images_sha256": references, "point_frames": list(point_frames),
                "panel_order": list(PANEL_NAMES), "point_order": list(POINT_NAMES),
                "panels_tl_tr_br_bl": points.tolist()}
        path = folder/REGIONS_NAME
        # Replace only the small selection sidecar; never alter recorded pixels.
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(data, indent=2)+"\n", encoding="utf-8")
        temporary.replace(path)
        return cls.load(folder, undistorter.path, required=True)

    def for_undistorter(self, undistorter, size):
        if tuple(size) != self.size:
            raise ValueError("Frame size differs from the manually marked image")
        if digest(undistorter.path) != self.data["intrinsics_sha256"]:
            raise ValueError("Manual regions require their original intrinsic coefficients")
        # Alpha changes only the output projection. Map marked points exactly
        # between output camera matrices instead of reusing old pixel positions.
        projection = undistorter.geometry(size)[1] @ np.linalg.inv(self.output_matrix)
        return cv.perspectiveTransform(self.quads.reshape(1, -1, 2), projection).reshape(4, 4, 2)
