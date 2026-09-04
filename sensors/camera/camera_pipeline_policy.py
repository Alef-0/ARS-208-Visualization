"""Compatibility imports for camera pipeline and timestamp policy users."""

from sensors.camera.camera_pipeline import (
    BACKENDS,
    CPU_BACKEND,
    DECODER_ENVIRONMENT_VARIABLE,
    ORIN_BACKEND,
    RTX_BACKEND,
    CameraDecoderBackend,
    available_decoder_backends,
    build_camera_pipeline,
    is_jetson_platform,
)
from sensors.camera.camera_timebase import (
    CAMERA_FRAME_RATE,
    FRAME_PERIOD_NS,
    FrameTimestampPolicy,
    FrameTimestampResult,
)


__all__ = (
    "BACKENDS",
    "CAMERA_FRAME_RATE",
    "CPU_BACKEND",
    "DECODER_ENVIRONMENT_VARIABLE",
    "FRAME_PERIOD_NS",
    "ORIN_BACKEND",
    "RTX_BACKEND",
    "CameraDecoderBackend",
    "FrameTimestampPolicy",
    "FrameTimestampResult",
    "available_decoder_backends",
    "build_camera_pipeline",
    "is_jetson_platform",
)
