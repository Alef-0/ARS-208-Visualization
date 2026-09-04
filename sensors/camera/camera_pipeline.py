"""Camera decoder discovery and GStreamer pipeline construction."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst


DECODER_ENVIRONMENT_VARIABLE = "SEGCOM_CAMERA_DECODER"


@dataclass(frozen=True)
class CameraDecoderBackend:
    name: str
    required_elements: tuple[str, ...]
    decoder_chain: str


RTX_BACKEND = CameraDecoderBackend(
    name="rtx",
    required_elements=("nvh264dec", "cudaconvert", "cudadownload", "videoconvert"),
    decoder_chain=(
        "nvh264dec ! cudaconvert ! cudadownload ! videoconvert ! "
        "video/x-raw,format=BGR"
    ),
)
ORIN_BACKEND = CameraDecoderBackend(
    name="orin",
    required_elements=("nvv4l2decoder", "nvvidconv", "videoconvert"),
    decoder_chain=(
        "nvv4l2decoder ! nvvidconv ! video/x-raw,format=BGRx ! "
        "videoconvert ! video/x-raw,format=BGR"
    ),
)
CPU_BACKEND = CameraDecoderBackend(
    name="cpu",
    required_elements=("avdec_h264", "videoconvert"),
    decoder_chain="avdec_h264 ! videoconvert ! video/x-raw,format=BGR",
)
BACKENDS = {
    backend.name: backend
    for backend in (RTX_BACKEND, ORIN_BACKEND, CPU_BACKEND)
}


def is_jetson_platform() -> bool:
    if Path("/etc/nv_tegra_release").exists():
        return True
    try:
        compatible = Path("/proc/device-tree/compatible").read_bytes().lower()
    except OSError:
        return False
    return b"nvidia" in compatible or b"tegra" in compatible


def available_decoder_backends(
    preference: str | None = None,
    *,
    factory_find: Callable[[str], object | None] = Gst.ElementFactory.find,
    jetson: bool | None = None,
) -> tuple[CameraDecoderBackend, ...]:
    """Return usable decoders in preferred order, with CPU as a fallback if present."""

    requested = (preference or os.getenv(DECODER_ENVIRONMENT_VARIABLE, "auto")).lower()
    if requested not in (*BACKENDS, "auto"):
        print(
            f"[DEBUG][CAMERA] Ignoring invalid {DECODER_ENVIRONMENT_VARIABLE}="
            f"{requested!r}; expected auto, rtx, orin, or cpu"
        )
        requested = "auto"

    if requested == "auto":
        use_jetson_order = is_jetson_platform() if jetson is None else jetson
        order = (ORIN_BACKEND, RTX_BACKEND, CPU_BACKEND) if use_jetson_order else (
            RTX_BACKEND,
            ORIN_BACKEND,
            CPU_BACKEND,
        )
    else:
        selected = BACKENDS[requested]
        order = (selected,) if selected is CPU_BACKEND else (selected, CPU_BACKEND)

    available = []
    for backend in order:
        if backend in available:
            continue
        if all(factory_find(element) is not None for element in backend.required_elements):
            available.append(backend)
        elif requested == backend.name:
            print(
                f"[DEBUG][CAMERA] Requested {backend.name} decoder is unavailable; "
                "trying the available fallback"
            )

    if not available:
        raise RuntimeError("No usable GStreamer H.264 decoder backend is available")
    return tuple(available)


def build_camera_pipeline(
    backend: CameraDecoderBackend,
    *,
    display_width: int,
    display_height: int,
    latency_ms: int,
) -> str:
    """Build the low-latency display and full-resolution capture pipeline."""

    return (
        f"rtspsrc name=source latency={latency_ms} protocols=tcp+udp "
        "buffer-mode=1 do-retransmission=true ! "
        f"rtph264depay ! h264parse ! {backend.decoder_chain} ! tee name=video "
        "video. ! queue leaky=downstream max-size-buffers=1 ! videoscale ! "
        f"video/x-raw,format=BGR,width={display_width},height={display_height} ! "
        "appsink name=display_sink emit-signals=true sync=false max-buffers=1 drop=true "
        "video. ! queue name=capture_queue max-size-buffers=30 "
        "max-size-bytes=0 max-size-time=0 ! "
        "video/x-raw,format=BGR ! "
        "appsink name=capture_sink emit-signals=true sync=false"
    )
