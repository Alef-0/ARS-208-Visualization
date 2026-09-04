"""QR creation and QReader decoding for camera calibration."""

from __future__ import annotations

from dataclasses import dataclass
from functools import wraps

import numpy as np
import qrcode
from qrcode.constants import ERROR_CORRECT_L


PAYLOAD_DIGITS = 12
PAYLOAD_MODULUS_MS = 10**PAYLOAD_DIGITS
QUIET_ZONE_MODULES = 2


def timestamp_payload(timestamp_ns: int) -> str:
    """Encode monotonic milliseconds in the compact calibration payload."""
    return f"{timestamp_ns // 1_000_000 % PAYLOAD_MODULUS_MS:0{PAYLOAD_DIGITS}d}"


def qr_matrix(payload: str, border: int = QUIET_ZONE_MODULES) -> np.ndarray:
    """Return a black/white QR matrix, including the requested quiet zone."""
    code = qrcode.QRCode(
        version=1,
        error_correction=ERROR_CORRECT_L,
        box_size=1,
        border=border,
    )
    code.add_data(payload, optimize=0)
    code.make(fit=True)
    return np.asarray(code.get_matrix(), dtype=np.uint8)


def decode_qrs(reader, image: np.ndarray) -> list[dict]:
    """Decode every QReader detection and retain its bounding-box geometry."""
    decoded, detections = reader.detect_and_decode(
        image=image,
        return_detections=True,
        is_bgr=True,
    )
    results = []
    for raw, detection in zip(decoded, detections or ()):
        box = np.asarray(detection["bbox_xyxy"], dtype=float).reshape(4)
        x1, y1, x2, y2 = box.tolist()
        results.append({
            "raw": raw,
            "bbox": box,
            "center": ((x1 + x2) / 2, (y1 + y2) / 2),
            "confidence": float(detection.get("confidence", 0.0)),
        })
    return results


def decode_qrs_with_quadrant_retries(reader, image: np.ndarray) -> list[dict]:
    """Decode the full image, then retry only quadrants with no decoded value."""
    height, width = image.shape[:2]
    results = decode_qrs(reader, image)
    found = {
        quadrant_for(result["center"], (width, height)).index
        for result in results
        if result["raw"] is not None
    }
    bounds = (
        (0, 0, width // 2, height // 2),
        (width // 2, 0, width, height // 2),
        (width // 2, height // 2, width, height),
        (0, height // 2, width // 2, height),
    )
    for quadrant, (left, top, right, bottom) in enumerate(bounds):
        if quadrant in found:
            continue
        for detection in decode_qrs(reader, image[top:bottom, left:right]):
            detection["bbox"] += np.array((left, top, left, top), dtype=float)
            detection["center"] = (
                detection["center"][0] + left,
                detection["center"][1] + top,
            )
            if quadrant_for(detection["center"], (width, height)).index == quadrant:
                results.append(detection)
    return results


def create_qreader():
    """Create QReader while adapting QRDet's legacy Ultralytics precision argument."""
    from qreader import QReader

    reader = QReader(min_confidence=0.3)
    predict = reader.detector.model.predict

    @wraps(predict)
    def predict_with_quantize(*args, **kwargs):
        kwargs.pop("half", None)
        kwargs.setdefault("quantize", None)
        return predict(*args, **kwargs)

    reader.detector.model.predict = predict_with_quantize
    return reader


@dataclass(frozen=True, slots=True)
class Quadrant:
    index: int
    name: str


QUADRANTS = (
    Quadrant(0, "Top-left"),
    Quadrant(1, "Top-right"),
    Quadrant(2, "Bottom-right"),
    Quadrant(3, "Bottom-left"),
)


def quadrant_for(center: tuple[float, float], size: tuple[int, int]) -> Quadrant:
    """Classify a detection by its bounding-box center."""
    x, y = center
    width, height = size
    if y < height / 2:
        return QUADRANTS[0 if x < width / 2 else 1]
    return QUADRANTS[3 if x < width / 2 else 2]


def order_by_quadrant(detections: list[dict], size: tuple[int, int]) -> list[dict]:
    """Attach sectors and order detections clockwise, then spatially within a sector."""
    ordered = []
    for detection in detections:
        quadrant = quadrant_for(detection["center"], size)
        ordered.append({**detection, "quadrant": quadrant.index, "quadrant_name": quadrant.name})
    return sorted(ordered, key=lambda item: (
        item["quadrant"], item["center"][1], item["center"][0]
    ))
