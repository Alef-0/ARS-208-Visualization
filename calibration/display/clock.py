"""Fullscreen monotonic EAN-13 display for camera-delay calibration."""

from __future__ import annotations

import argparse
import os
import time
from dataclasses import dataclass

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame

if __package__:
    from .ean13 import (
        MAX_PIXEL_VALUE,
        MIN_PIXEL_VALUE,
        EAN13Painter,
        monotonic_ms_payload,
    )
    from .timing import DisplayJournal, FramePacer
else:
    from ean13 import (
        MAX_PIXEL_VALUE,
        MIN_PIXEL_VALUE,
        EAN13Painter,
        monotonic_ms_payload,
    )
    from timing import DisplayJournal, FramePacer


WINDOW_NAME = "Calibration Clock"
BACKGROUND_COLOR = (MIN_PIXEL_VALUE,) * 3
FOREGROUND_COLOR = (MAX_PIXEL_VALUE,) * 3
CORNER_COUNT = 4
DEFAULT_VISIBLE_FRAMES = 3
BARCODE_PADDING_X = 24
BARCODE_PADDING_Y = 32
UNDERLINE_HEIGHT = 4
UNDERLINE_COLOR = (255, 255, 255)
DISPLAY_REFRESH_HZ = 60


def format_monotonic_timestamp(timestamp_ns: int) -> str:
    """Format monotonic time as grouped seconds plus milliseconds."""
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    return f"{seconds:,}".replace(",", " ") + f".{nanoseconds // 1_000_000:03d}"


@dataclass(frozen=True, slots=True)
class CornerLayout:
    area: pygame.Rect
    barcode: pygame.Rect
    underline: pygame.Rect
    clock_center: tuple[int, int]


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
    padding_x = min(BARCODE_PADDING_X, (area.width - 113) // 2)
    padding_y = min(BARCODE_PADDING_Y, area.height // 10)
    barcode.inflate_ip(-padding_x * 2, -padding_y * 2)
    underline = pygame.Rect(0, clock_area.bottom - UNDERLINE_HEIGHT - 2,
                            max(40, area.width // 3), UNDERLINE_HEIGHT)
    underline.centerx = clock_area.centerx
    return CornerLayout(area=area, barcode=barcode, underline=underline,
                        clock_center=(clock_area.centerx, clock_area.centery - 4))


class CalibrationRenderer:
    """Retain a configurable marker history and clear expired quadrants."""

    def __init__(self, target: pygame.Surface, visible_frames: int = DEFAULT_VISIBLE_FRAMES):
        if type(visible_frames) is not int or not 1 <= visible_frames <= CORNER_COUNT:
            raise ValueError("Visible frames must be a whole number between 1 and 4")
        self.visible_frames = visible_frames
        width, height = target.get_size()
        if width < 320 or height < 160:
            raise ValueError("Canvas must be at least 320 by 160 pixels")

        if not pygame.font.get_init():
            pygame.font.init()
        font_size = max(22, min(width // 32, height // 18))
        self._font = pygame.font.Font(None, font_size)
        clock_height = self._font.get_linesize() + 12

        self.target = target
        self.size = target.get_size()
        self._layouts = tuple(
            _make_layout(area, corner, clock_height)
            for corner, area in enumerate(_corner_areas(width, height))
        )
        self._painters = tuple(EAN13Painter(layout.barcode) for layout in self._layouts)
        self.timestamps = [None] * CORNER_COUNT
        self._next_corner = 0
        self._newest_corner = None
        target.fill(BACKGROUND_COLOR)

    def render_next(self, timestamp_ns: int) -> int:
        """Change one quadrant and the previous underline without clearing history."""
        corner = self._next_corner
        if self._newest_corner is not None:
            self.target.fill(BACKGROUND_COLOR, self._layouts[self._newest_corner].underline)
        if self.visible_frames < CORNER_COUNT:
            expired = (corner - self.visible_frames) % CORNER_COUNT
            if self.timestamps[expired] is not None:
                self.target.fill(BACKGROUND_COLOR, self._layouts[expired].area)
                self.timestamps[expired] = None
        layout = self._layouts[corner]
        self.target.fill(BACKGROUND_COLOR, layout.area)
        self._draw_corner(corner, timestamp_ns)
        self.target.fill(UNDERLINE_COLOR, layout.underline)
        self.timestamps[corner] = timestamp_ns
        self._newest_corner = corner
        self._next_corner = (corner + 1) % CORNER_COUNT
        return corner

    def _draw_corner(self, corner: int, timestamp_ns: int) -> None:
        layout = self._layouts[corner]
        self._painters[corner].draw(
            self.target,
            monotonic_ms_payload(timestamp_ns),
            dark_color=BACKGROUND_COLOR,
            light_color=FOREGROUND_COLOR,
        )
        clock = self._font.render(
            format_monotonic_timestamp(timestamp_ns),
            True,
            FOREGROUND_COLOR,
            BACKGROUND_COLOR,
        )
        self.target.blit(clock, clock.get_rect(center=layout.clock_center))

    def metadata(self) -> dict:
        return {
            "size": list(self.size), "indicator_style": "underline",
            "indicator_width": UNDERLINE_HEIGHT,
            "visible_frames": self.visible_frames,
            "timestamp_semantics": "monotonic time sampled before drawing, not physical scanout",
            "corner_order": ["top-left", "top-right", "bottom-right", "bottom-left"],
            "layouts": [
                {"area": list(layout.area), "barcode": list(layout.barcode),
                 "bars": list(painter.bars), "underline": list(layout.underline)}
                for layout, painter in zip(self._layouts, self._painters)
            ],
        }


def run_calibration_clock(
    stop_event=None,
    *,
    width: int = 1920,
    height: int = 1080,
    windowed: bool = False,
    refresh_hz: float = DISPLAY_REFRESH_HZ,
    journal_path: str | None = None,
    visible_frames: int = DEFAULT_VISIBLE_FRAMES,
) -> None:
    """Sample/draw/present on one thread, with no prefetched or queued frames."""
    if width <= 0 or height <= 0:
        raise ValueError("Canvas dimensions must be positive")

    pygame.display.init()
    pygame.font.init()
    try:
        flags = pygame.SCALED | (0 if windowed else pygame.FULLSCREEN)
        try:
            screen = pygame.display.set_mode((width, height), flags, vsync=1)
        except pygame.error as error:
            raise RuntimeError("Pygame could not create the calibration display") from error

        pygame.display.set_caption(WINDOW_NAME)
        renderer = CalibrationRenderer(screen, visible_frames=visible_frames)
        # In Pygame 2.6.1 SCALED mode, update(rect) calls flip internally anyway.
        # Dirty drawing saves CPU work; one flip presents the complete state.
        pygame.display.flip()
        pacer = FramePacer(time.monotonic_ns(), refresh_hz)
        clock = pygame.time.Clock()
        journal = DisplayJournal(journal_path, {
            **renderer.metadata(), "requested_refresh_hz": refresh_hz,
            "pygame_version": pygame.version.ver,
            "sdl_version": list(pygame.get_sdl_version()),
            "display_driver": pygame.display.get_driver(), "vsync_requested": True,
        })

        paused = False
        exit_requested = False
        resumed_after_pause = False

        def poll_controls():
            nonlocal paused, exit_requested, resumed_after_pause, pacer
            if stop_event is not None and stop_event.is_set():
                exit_requested = True
                return True
            toggled = False
            for event in pygame.event.get():
                if event.type == pygame.QUIT or (
                    event.type == pygame.KEYDOWN and event.key in (pygame.K_q, pygame.K_ESCAPE)
                ):
                    exit_requested = True
                    return True
                if (event.type == pygame.KEYDOWN and event.key == pygame.K_p
                        and not getattr(event, "repeat", False)):
                    paused = not paused
                    toggled = True
                    journal.pause(paused, time.monotonic_ns())
                    if not paused:
                        # Start a fresh cadence; intentional idle time is not
                        # missed refreshes, nor an interval to learn from.
                        pacer = FramePacer(time.monotonic_ns(), refresh_hz)
                        clock.tick(0)
                        resumed_after_pause = True
                    pygame.display.set_caption(WINDOW_NAME + (" — paused (P to resume)" if paused else ""))
                    print("[CALIBRATION] " + (
                        "Paused for inspection; P resumes. Camera recording is unchanged."
                        if paused else "Resumed with a fresh display timing schedule."
                    ), flush=True)
            # Even a pause/resume pair in one event batch must abort the old wait.
            return exit_requested or paused or toggled

        try:
            while True:
                if paused:
                    poll_controls()
                    if exit_requested:
                        break
                    if paused:
                        clock.tick(30)  # Keep controls responsive without drawing or spinning.
                    continue
                ready, skipped = pacer.wait(poll_controls)
                if not ready:
                    if exit_requested:
                        break
                    continue
                marker_ns = time.monotonic_ns()
                corner = renderer.render_next(marker_ns)
                submit_ns = time.monotonic_ns()
                pygame.display.flip()
                returned_ns = time.monotonic_ns()
                timing = pacer.observe(marker_ns, submit_ns, returned_ns, skipped)
                clock.tick(0)  # Pygame bookkeeping only: no second frame limiter.
                timing["pygame_frame_ms"] = clock.get_time()
                timing["resumed_after_pause"] = resumed_after_pause
                resumed_after_pause = False
                journal.append(corner, timing)
        finally:
            journal.close()
    finally:
        pygame.quit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Display rotating monotonic EAN-13 camera-calibration frames."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--windowed", action="store_true")
    parser.add_argument("--refresh-hz", type=float, default=DISPLAY_REFRESH_HZ)
    parser.add_argument("--journal", help="New JSONL timing journal path (never overwritten)")
    parser.add_argument("--visible-frames", type=int, choices=range(1, 5),
                        default=DEFAULT_VISIBLE_FRAMES,
                        help="Visible barcode history length (default: 3, leaving the next quadrant blank)")
    arguments = parser.parse_args()
    run_calibration_clock(
        width=arguments.width,
        height=arguments.height,
        windowed=arguments.windowed,
        refresh_hz=arguments.refresh_hz,
        journal_path=arguments.journal,
        visible_frames=arguments.visible_frames,
    )


if __name__ == "__main__":
    main()
