"""Independent EAN readers for recorded pixels; neither opens a camera/window."""

import ctypes as C
from ctypes.util import find_library

import cv2 as cv
import numpy as np


class ZBarReader:
    """Use the optional system libzbar without requiring a Python wrapper."""

    def __init__(self):
        library = find_library("zbar")
        if not library:
            raise RuntimeError("ZBar unavailable: system libzbar was not found")
        self.lib = C.CDLL(library)
        ptr, uint, integer, ulong = C.c_void_p, C.c_uint, C.c_int, C.c_ulong
        signatures = {
            "image_scanner_create": (ptr, []),
            "image_scanner_destroy": (None, [ptr]),
            "image_scanner_set_config": (integer, [ptr, integer, integer, integer]),
            "image_create": (ptr, []), "image_destroy": (None, [ptr]),
            "image_set_format": (None, [ptr, ulong]),
            "image_set_size": (None, [ptr, uint, uint]),
            "image_set_data": (None, [ptr, ptr, ulong, ptr]),
            "scan_image": (integer, [ptr, ptr]),
            "image_first_symbol": (ptr, [ptr]), "symbol_next": (ptr, [ptr]),
            "symbol_get_data": (C.c_char_p, [ptr]),
            "symbol_get_type": (integer, [ptr]),
            "symbol_get_quality": (integer, [ptr]),
            "symbol_get_loc_size": (uint, [ptr]),
            "symbol_get_loc_x": (integer, [ptr, uint]),
            "symbol_get_loc_y": (integer, [ptr, uint]),
        }
        for name, (result, arguments) in signatures.items():
            fn = getattr(self.lib, "zbar_" + name)
            fn.restype, fn.argtypes = result, arguments
        self.scanner = self.lib.zbar_image_scanner_create()
        if not self.scanner:
            raise RuntimeError("ZBar could not create its scanner")
        self.lib.zbar_image_scanner_set_config(self.scanner, 0, 0, 0)
        self.lib.zbar_image_scanner_set_config(self.scanner, 13, 0, 1)

    def decode(self, frame):
        gray = np.ascontiguousarray(cv.cvtColor(frame, cv.COLOR_BGR2GRAY))
        lib, image = self.lib, self.lib.zbar_image_create()
        if not image:
            raise RuntimeError("ZBar could not allocate an image")
        try:
            lib.zbar_image_set_format(image, int.from_bytes(b"Y800", "little"))
            lib.zbar_image_set_size(image, gray.shape[1], gray.shape[0])
            lib.zbar_image_set_data(image, gray.ctypes.data, gray.nbytes, None)
            if lib.zbar_scan_image(self.scanner, image) < 0:
                raise RuntimeError("ZBar failed to scan the image")
            symbols, symbol = [], lib.zbar_image_first_symbol(image)
            while symbol:
                points = [[lib.zbar_symbol_get_loc_x(symbol, i), lib.zbar_symbol_get_loc_y(symbol, i)]
                          for i in range(lib.zbar_symbol_get_loc_size(symbol))]
                symbols.append({
                    "raw_code": lib.zbar_symbol_get_data(symbol).decode("ascii", errors="replace"),
                    "type": "EAN_13" if lib.zbar_symbol_get_type(symbol) == 13 else "other",
                    "quality": lib.zbar_symbol_get_quality(symbol),
                    "points": cv.convexHull(np.int32(points)).reshape(-1, 2).tolist() if points else [],
                    "location_count": len(points),
                })
                symbol = lib.zbar_symbol_next(symbol)
            return symbols
        finally:
            lib.zbar_image_destroy(image)

    def close(self):
        if self.scanner:
            self.lib.zbar_image_scanner_destroy(self.scanner)
            self.scanner = None


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

    def close(self):
        pass
