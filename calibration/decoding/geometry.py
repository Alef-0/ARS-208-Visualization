"""Lens correction and coordinate mapping shared by offline calibration tools."""

import json
from pathlib import Path

import cv2 as cv
import numpy as np


class Undistorter:
    def __init__(self, path, calibration_size=None, alpha=0.0):
        self.path = Path(path).expanduser().resolve()
        self.alpha = float(alpha)
        if not np.isfinite(self.alpha) or not 0 <= self.alpha <= 1:
            raise ValueError("Undistortion alpha must be between 0 and 1")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.matrix = np.asarray(data["camera_matrix"], dtype=np.float64)
        self.distortion = np.asarray(data["dist_coeffs"], dtype=np.float64).reshape(-1)
        if (self.matrix.shape != (3, 3) or not np.isfinite(self.matrix).all()
                or self.matrix[0, 0] <= 0 or self.matrix[1, 1] <= 0
                or not np.allclose(self.matrix[2], [0, 0, 1])
                or self.distortion.size not in (4, 5, 8, 12, 14)
                or not np.isfinite(self.distortion).all()):
            raise ValueError("Invalid camera_matrix or dist_coeffs in the intrinsic file")
        size = calibration_size or data.get("image_size")
        self.calibration_size = tuple(map(int, size)) if size is not None else None
        if self.calibration_size and (len(self.calibration_size) != 2 or min(self.calibration_size) <= 0):
            raise ValueError("Calibration resolution must contain positive width and height")
        self.maps = {}

    def geometry(self, size):
        size = tuple(size)
        if size not in self.maps:
            matrix = self.matrix.copy()
            if self.calibration_size:
                matrix[0, :] *= size[0] / self.calibration_size[0]
                matrix[1, :] *= size[1] / self.calibration_size[1]
            output, _ = cv.getOptimalNewCameraMatrix(matrix, self.distortion, size, self.alpha, size)
            if not np.isfinite(output).all() or min(output[0, 0], output[1, 1]) <= 0:
                raise ValueError("Intrinsics produce invalid undistortion geometry")
            maps = cv.initUndistortRectifyMap(matrix, self.distortion, None, output, size, cv.CV_32FC1)
            self.maps[size] = matrix, output, maps
        return self.maps[size]

    def image(self, frame):
        _, _, maps = self.geometry((frame.shape[1], frame.shape[0]))
        return cv.remap(frame, *maps, cv.INTER_LINEAR)

    def valid_pixels(self, size):
        """Exclude black padding introduced by undistortion from optical evidence."""
        mx, my = self.geometry(size)[2]
        return ((mx >= 0) & (mx <= size[0]-1) & (my >= 0) & (my <= size[1]-1))

    def points(self, points, source, target, size):
        points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
        if source == target or not len(points):
            return points.copy()
        matrix, output, _ = self.geometry(size)
        if source == "Original":
            criteria = (cv.TERM_CRITERIA_COUNT | cv.TERM_CRITERIA_EPS, 40, 1e-8)
            iterative = getattr(cv, "undistortPointsIter", None)
            if iterative is not None:  # OpenCV 4.x exposes the overload separately.
                return iterative(points[:, None], matrix, self.distortion,
                                 None, output, criteria).reshape(-1, 2)
            return cv.undistortPoints(points[:, None], matrix, self.distortion,
                                      P=output, criteria=criteria).reshape(-1, 2)
        homogeneous = np.column_stack((points, np.ones(len(points))))
        rays = homogeneous @ np.linalg.inv(output).T
        return cv.projectPoints(rays, np.zeros(3), np.zeros(3), matrix, self.distortion)[0].reshape(-1, 2)
