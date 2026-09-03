"""OpenCV-only decoding, with journal-validated regions learned per recording."""

from collections import defaultdict, deque

import cv2 as cv
import numpy as np

from calibration.display.ean13 import ean13_check_digit


def normalized_code(code, kind="EAN_13"):
    code = code.strip()
    if kind in {"UPC_A", "UPC-A"} and len(code) == 12:
        code = "0" + code
    if (kind not in {"EAN_13", "EAN-13", "UPC_A", "UPC-A"}
            or len(code) != 13 or not code.isascii() or not code.isdigit()
            or ean13_check_digit(code[:12]) != code[-1]):
        return None
    return code


class OpenCVReader:
    def __init__(self):
        factory = getattr(cv, "barcode_BarcodeDetector", None)
        if factory is None:
            raise RuntimeError("This OpenCV installation has no BarcodeDetector")
        self.detector = factory()

    def decode(self, frame):
        found, codes, types, points = self.detector.detectAndDecodeWithType(frame)
        if not found or points is None:
            return []
        return [{"raw_code": code, "type": kind, "points": quad.tolist()}
                for code, kind, quad in zip(codes, types, points) if code]

    def decode_regions(self, gray, regions, binary=False):
        """Decode supplied BL/TL/TR/BR regions, without redetecting merged panels."""
        results = []
        for corner, quad in regions:
            quad = np.float32(quad)
            # Independent bands preserve differing generations in a torn panel.
            for low, high in ((.1, .35), (.375, .625), (.65, .9)):
                band = np.float32([quad[1]*(1-high)+quad[0]*high,
                                   quad[1]*(1-low)+quad[0]*low,
                                   quad[2]*(1-low)+quad[3]*low,
                                   quad[2]*(1-high)+quad[3]*high])
                pixels, points = gray, band
                if binary:
                    x, y = np.maximum(0, np.floor(band.min(axis=0)).astype(int))
                    right, bottom = np.minimum(gray.shape[::-1], np.ceil(band.max(axis=0)).astype(int)+1)
                    crop = gray[y:bottom, x:right]
                    if crop.size == 0:
                        continue
                    pixels = cv.threshold(crop, 0, 255, cv.THRESH_BINARY | cv.THRESH_OTSU)[1]
                    points = band - (x, y)
                found, codes, kinds = self.detector.decodeWithType(pixels, np.float32(points)[None])
                if found:
                    results.extend({"raw_code": code, "type": kind, "points": band.tolist(),
                                    "region_corner": corner, "band": [low, high]}
                                   for code, kind in zip(codes, kinds) if code)
        return results

    def close(self):
        pass


class RegionDecoder:
    """Learn locations, never payloads. Reset on seeking, movement, or stale support."""

    def __init__(self, evidence, contrast=True, binary=False):
        self.evidence = evidence
        self.reader = OpenCVReader()
        self.contrast, self.binary = contrast, binary
        self.clahe = cv.createCLAHE(2, (8, 8))
        self.history = defaultdict(lambda: deque(maxlen=24))
        self.last_seen = {}
        self.last_index = None
        self.resets = 0

    def reset(self):
        self.history.clear()
        self.last_seen.clear()
        self.last_index = None
        self.resets += 1

    def regions(self):
        return [(corner, np.float32(np.median(points, axis=0)))
                for corner, points in sorted(self.history.items()) if points]

    def decode(self, frame, reference_ms, received_ns=None, index=None):
        if index is None:
            index = 0 if self.last_index is None else self.last_index + 1
        if self.last_index is not None and index != self.last_index + 1:
            self.reset()
        self.last_index = index
        gray = cv.cvtColor(frame, cv.COLOR_BGR2GRAY) if frame.ndim == 3 else frame
        variants = [("grayscale", gray)]
        if self.contrast:
            variants.append(("local_contrast", self.clahe.apply(gray)))
        discovered = []
        for name, pixels in variants:
            discovered.extend({**s, "preprocessing": name, "location_source": "detected"}
                              for s in self.reader.decode(pixels))

        def marker(symbol):
            code = normalized_code(symbol["raw_code"], symbol["type"])
            row = self.evidence.lookup(code, reference_ms) if self.evidence and code else None
            if row and (received_ns is None or row["marker_ns"] <= received_ns):
                return row
            return None

        # Full-frame discovery remains active even after acquisition. A new
        # camera position must not keep decoding yesterday's pixel locations.
        updates = defaultdict(list)
        for symbol in discovered:
            row = marker(symbol)
            if row:
                updates[row["corner"]].append(symbol["points"])
        candidates, shifts, outliers, narrowed = {}, {}, set(), set()
        for corner, quads in updates.items():
            # A detector can merge both top/bottom panels into one rectangle.
            # Prefer the smallest supporting box, not their averaged geometry.
            new = np.float32(min(quads, key=lambda q: abs(cv.contourArea(np.float32(q)))))
            candidates[corner] = new
            if self.history[corner]:
                old = np.median(self.history[corner], axis=0)
                scale = max(1., np.linalg.norm(old[2]-old[1]))
                width_ratio = np.linalg.norm(new[2]-new[1])/scale
                height_ratio = np.linalg.norm(new[0]-new[1])/max(1., np.linalg.norm(old[0]-old[1]))
                shift = new.mean(axis=0)-old.mean(axis=0)
                if (.25 < width_ratio < .8 and height_ratio < 1.3
                        and cv.pointPolygonTest(np.float32(old), tuple(map(float, new.mean(axis=0))), False) >= 0):
                    narrowed.add(corner)
                elif np.linalg.norm(shift) > .12*scale or not .75 <= width_ratio <= 1.3 or height_ratio > 1.6:
                    outliers.add(corner)
                    if .75 <= width_ratio <= 1.3 and .65 <= height_ratio <= 1.6:
                        shifts[corner] = shift
        # Confirm motion in multiple panels. One merged rectangle must not
        # reset all four regions of a stationary recording.
        moved = False
        if len(shifts) >= 2:
            vectors = np.array(list(shifts.values()))
            median = np.median(vectors, axis=0)
            moved = bool(np.linalg.norm(median) > .04*frame.shape[1]
                         and np.max(np.linalg.norm(vectors-median, axis=1)) < .04*frame.shape[1])
        if moved:
            self.reset()
            self.last_index = index
            outliers.clear()
        for corner in list(self.history):
            if index-self.last_seen.get(corner, index) > 60:
                del self.history[corner]
        # One geometry vote per corner per frame, not per preprocessing variant.
        for corner, quad in candidates.items():
            if corner in outliers:
                continue
            if corner in narrowed:
                self.history[corner].clear()
            self.history[corner].append(quad)
            self.last_seen[corner] = index
        regions = self.regions()
        symbols = list(discovered)
        for name, pixels in variants:
            symbols.extend({**s, "preprocessing": name, "location_source": "learned_region"}
                           for s in self.reader.decode_regions(pixels, regions))
        if self.binary:
            symbols.extend({**s, "preprocessing": "local_otsu", "location_source": "learned_region"}
                           for s in self.reader.decode_regions(gray, regions, binary=True))
        result, seen, corner_codes = [], set(), defaultdict(set)
        for symbol in symbols:
            row = marker(symbol)
            if row and symbol.get("region_corner", row["corner"]) != row["corner"]:
                continue
            code = normalized_code(symbol["raw_code"], symbol["type"])
            key = (code or symbol["raw_code"], symbol["preprocessing"],
                   symbol["location_source"], symbol.get("region_corner"))
            if key in seen:
                continue
            seen.add(key)
            if row:
                corner_codes[row["corner"]].add(row["index"])
            result.append(symbol)
        for symbol in result:
            row = marker(symbol)
            symbol["transition"] = bool(row and len(corner_codes[row["corner"]]) > 1)
        return result
