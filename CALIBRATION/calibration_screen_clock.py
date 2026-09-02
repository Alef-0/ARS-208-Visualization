"""Fullscreen monotonic EAN-13 display for camera-delay calibration."""

from __future__ import annotations

import argparse
import os
import queue
import threading
import time
from dataclasses import dataclass

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

if __package__:
    from .ean13 import (
        MAX_PIXEL_VALUE,
        MIN_PIXEL_VALUE,
        draw_ean13,
        monotonic_ms_payload,
    )
else:
    from ean13 import (
        MAX_PIXEL_VALUE,
        MIN_PIXEL_VALUE,
        draw_ean13,
        monotonic_ms_payload,
    )


WINDOW_NAME = "Calibration Clock"
BACKGROUND_COLOR = (MIN_PIXEL_VALUE,) * 3
FOREGROUND_COLOR = (MAX_PIXEL_VALUE,) * 3
CORNER_COUNT = 4
PERSISTED_FRAME_COUNT = 3
BARCODE_PADDING = 8
DISPLAY_REFRESH_HZ = 60
DISPLAY_FRAME_NS = round(1_000_000_000 / DISPLAY_REFRESH_HZ)
FRAME_WAIT_SECONDS = 0.05
FRAME_BUFFER_COUNT = 2


def predicted_display_time_ns(render_started_ns: int) -> int:
    """Estimate when a rendered frame reaches the next 60 Hz display flip."""
    return render_started_ns + DISPLAY_FRAME_NS


def format_monotonic_timestamp(timestamp_ns: int) -> str:
    """Format monotonic time as grouped seconds plus milliseconds."""
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    return f"{seconds:,}".replace(",", " ") + f".{nanoseconds // 1_000_000:03d}"


@dataclass(frozen=True, slots=True)
class CornerLayout:
    barcode: pygame.Rect
    clock_center: tuple[int, int]


@dataclass(slots=True)
class CornerState:
    timestamp_ns: int | None = None
    remaining_frames: int = 0


@dataclass(frozen=True, slots=True)
class CompletedFrame:
    """A complete off-screen frame temporarily owned by the display thread."""

    surface: pygame.Surface
    corner: int
    timestamp_ns: int


def _corner_areas(width: int, height: int) -> tuple[pygame.Rect, ...]:
    left_width = width // 2
    top_height = height // 2
    return (
        pygame.Rect(0, 0, left_width, top_height),
        pygame.Rect(left_width, 0, width - left_width, top_height),
        pygame.Rect(
            left_width,
            top_height,
            width - left_width,
            height - top_height,
        ),
        pygame.Rect(0, top_height, left_width, height - top_height),
    )


def _make_layout(area: pygame.Rect, corner: int, clock_height: int) -> CornerLayout:
    barcode = area.copy()
    if corner in (0, 1):
        clock_area = pygame.Rect(area.x, area.y, area.width, clock_height)
        barcode.y += clock_height
    else:
        clock_area = pygame.Rect(
            area.x,
            area.bottom - clock_height,
            area.width,
            clock_height,
        )
    barcode.height -= clock_height
    barcode.inflate_ip(-BARCODE_PADDING * 2, -BARCODE_PADDING * 2)
    return CornerLayout(barcode=barcode, clock_center=clock_area.center)


class CalibrationRenderer:
    """Render the rotating barcode sequence into caller-owned frame buffers."""

    def __init__(self, size: tuple[int, int]):
        width, height = size
        if width < 240 or height < 160:
            raise ValueError("Canvas must be at least 240 by 160 pixels")

        if not pygame.font.get_init():
            pygame.font.init()
        font_size = max(22, min(width // 32, height // 18))
        self._font = pygame.font.Font(None, font_size)
        clock_height = self._font.get_linesize() + 4

        self.size = size
        self._layouts = tuple(
            _make_layout(area, corner, clock_height)
            for corner, area in enumerate(_corner_areas(width, height))
        )
        self._states = [CornerState() for _ in range(CORNER_COUNT)]
        self._next_corner = 0

    def render_next(self, target: pygame.Surface, timestamp_ns: int) -> int:
        """Draw the next complete sequence frame and return its updated corner."""
        if target.get_size() != self.size:
            raise ValueError("Target surface does not match the renderer size")

        corner = self._next_corner
        state = self._states[corner]
        state.timestamp_ns = timestamp_ns
        state.remaining_frames = PERSISTED_FRAME_COUNT

        target.fill(BACKGROUND_COLOR)
        for layout, corner_state in zip(self._layouts, self._states):
            if corner_state.remaining_frames == 0:
                continue
            self._draw_corner(target, layout, corner_state.timestamp_ns)

        for corner_state in self._states:
            corner_state.remaining_frames = max(0, corner_state.remaining_frames - 1)
        self._next_corner = (corner + 1) % CORNER_COUNT
        return corner

    def _draw_corner(
        self,
        target: pygame.Surface,
        layout: CornerLayout,
        timestamp_ns: int | None,
    ) -> None:
        if timestamp_ns is None:
            return
        draw_ean13(
            target,
            monotonic_ms_payload(timestamp_ns),
            layout.barcode,
            dark_color=BACKGROUND_COLOR,
            light_color=FOREGROUND_COLOR,
        )
        clock = self._font.render(
            format_monotonic_timestamp(timestamp_ns),
            True,
            FOREGROUND_COLOR,
            BACKGROUND_COLOR,
        )
        target.blit(clock, clock.get_rect(center=layout.clock_center))


class CalibrationFrameProducer:
    """Prepare frames on a worker without modifying display-owned buffers."""

    def __init__(self, size: tuple[int, int]):
        self._size = size
        self._available: queue.Queue[pygame.Surface] = queue.Queue(
            maxsize=FRAME_BUFFER_COUNT
        )
        self._ready: queue.Queue[CompletedFrame] = queue.Queue(maxsize=1)
        for _ in range(FRAME_BUFFER_COUNT):
            surface = pygame.Surface(size, depth=32)
            surface.fill(BACKGROUND_COLOR)
            self._available.put_nowait(surface)

        self._stop_event = threading.Event()
        self._failure: Exception | None = None
        self._thread = threading.Thread(
            target=self._produce_frames,
            name="calibration-frame-producer",
        )
        self._started = False

    def start(self) -> None:
        if self._started:
            raise RuntimeError("Calibration frame producer is already started")
        self._started = True
        self._thread.start()

    def next_frame(self, timeout: float = FRAME_WAIT_SECONDS) -> CompletedFrame | None:
        """Give the display thread ownership of the next complete frame."""
        if not self._started:
            raise RuntimeError("Calibration frame producer is not started")
        try:
            return self._ready.get(timeout=timeout)
        except queue.Empty:
            self._raise_if_failed()
            return None

    def release(self, frame: CompletedFrame) -> None:
        """Return a frame after display so its buffer may be rendered again."""
        self._available.put_nowait(frame.surface)

    def stop(self) -> None:
        if not self._started:
            return
        self._stop_event.set()
        self._thread.join(timeout=1.0)
        if self._thread.is_alive():
            raise RuntimeError("Calibration frame producer did not stop")

    def _produce_frames(self) -> None:
        try:
            renderer = CalibrationRenderer(self._size)
            while not self._stop_event.is_set():
                surface = self._wait_for_available_surface()
                if surface is None:
                    return

                timestamp_ns = predicted_display_time_ns(time.monotonic_ns())
                corner = renderer.render_next(surface, timestamp_ns)
                completed = CompletedFrame(surface, corner, timestamp_ns)
                if not self._publish(completed):
                    return
        except Exception as error:
            self._failure = error
            self._stop_event.set()

    def _wait_for_available_surface(self) -> pygame.Surface | None:
        while not self._stop_event.is_set():
            try:
                return self._available.get(timeout=FRAME_WAIT_SECONDS)
            except queue.Empty:
                continue
        return None

    def _publish(self, frame: CompletedFrame) -> bool:
        while not self._stop_event.is_set():
            try:
                self._ready.put(frame, timeout=FRAME_WAIT_SECONDS)
                return True
            except queue.Full:
                continue
        return False

    def _raise_if_failed(self) -> None:
        if self._failure is not None:
            raise RuntimeError("Calibration frame producer failed") from self._failure


def _exit_requested() -> bool:
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            return True
        if event.type == pygame.KEYDOWN and event.key in (pygame.K_q, pygame.K_ESCAPE):
            return True
    return False


def run_calibration_clock(
    stop_event=None,
    *,
    width: int = 1920,
    height: int = 1080,
    windowed: bool = False,
) -> None:
    """Run display work here and frame creation on one dedicated worker thread."""
    if width <= 0 or height <= 0:
        raise ValueError("Canvas dimensions must be positive")

    pygame.display.init()
    pygame.font.init()
    try:
        flags = pygame.DOUBLEBUF | pygame.SCALED | (0 if windowed else pygame.FULLSCREEN)
        try:
            screen = pygame.display.set_mode((width, height), flags, vsync=1)
        except pygame.error as error:
            raise RuntimeError("Pygame could not create the calibration display") from error

        pygame.display.set_caption(WINDOW_NAME)
        producer = CalibrationFrameProducer(screen.get_size())
        producer.start()
        try:
            while stop_event is None or not stop_event.is_set():
                if _exit_requested():
                    break
                completed = producer.next_frame()
                if completed is None:
                    continue
                screen.blit(completed.surface, (0, 0))
                pygame.display.flip()
                producer.release(completed)
        finally:
            producer.stop()
    finally:
        pygame.quit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Display rotating monotonic EAN-13 camera-calibration frames."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--windowed", action="store_true")
    arguments = parser.parse_args()
    run_calibration_clock(
        width=arguments.width,
        height=arguments.height,
        windowed=arguments.windowed,
    )


if __name__ == "__main__":
    main()
