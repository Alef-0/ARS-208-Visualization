import argparse
import time

import cv2 as cv
import numpy as np


WINDOW_NAME = "Calibration Clock"
BACKGROUND_COLOR = (0, 0, 0)
TEXT_COLOR = (255, 255, 255)
SECONDARY_TEXT_COLOR = (180, 180, 180)


def format_unix_timestamp(timestamp_ns: int) -> str:
    """Return Unix epoch time as seconds.milliseconds."""
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    milliseconds = nanoseconds // 1_000_000
    return f"{seconds}.{milliseconds:03d}"


def format_local_datetime(timestamp_ns: int) -> str:
    """Return local system time with millisecond precision."""
    seconds, nanoseconds = divmod(timestamp_ns, 1_000_000_000)
    milliseconds = nanoseconds // 1_000_000
    local_time = time.localtime(seconds)
    return time.strftime("%Y-%m-%d %H:%M:%S", local_time) + f".{milliseconds:03d}"


def centered_text(
    canvas: np.ndarray,
    text: str,
    y: int,
    font_scale: float,
    thickness: int,
    color: tuple[int, int, int],
) -> None:
    font = cv.FONT_HERSHEY_SIMPLEX
    (text_width, text_height), _ = cv.getTextSize(
        text,
        font,
        font_scale,
        thickness,
    )
    x = max(0, (canvas.shape[1] - text_width) // 2)
    baseline_y = y + text_height // 2

    cv.putText(
        canvas,
        text,
        (x, baseline_y),
        font,
        font_scale,
        color,
        thickness,
        cv.LINE_AA,
    )


def run_calibration_clock(
    stop_event=None,
    *,
    width: int = 1920,
    height: int = 1080,
    windowed: bool = False,
) -> None:
    """Display the calibration clock until closed or asked to stop."""
    if width <= 0 or height <= 0:
        raise ValueError("Canvas dimensions must be positive")

    cv.namedWindow(WINDOW_NAME, cv.WINDOW_NORMAL)
    if windowed:
        cv.resizeWindow(WINDOW_NAME, width, height)
    else:
        cv.setWindowProperty(
            WINDOW_NAME,
            cv.WND_PROP_FULLSCREEN,
            cv.WINDOW_FULLSCREEN,
        )

    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    try:
        while stop_event is None or not stop_event.is_set():
            timestamp_ns = time.time_ns()
            canvas[:] = BACKGROUND_COLOR

            centered_text(
                canvas,
                "UNIX TIME (seconds.milliseconds)",
                int(height * 0.28),
                3.0,
                8,
                SECONDARY_TEXT_COLOR,
            )
            centered_text(
                canvas,
                format_unix_timestamp(timestamp_ns),
                int(height * 0.48),
                7.0,
                16,
                TEXT_COLOR,
            )
            centered_text(
                canvas,
                format_local_datetime(timestamp_ns),
                int(height * 0.70),
                3.0,
                8,
                SECONDARY_TEXT_COLOR,
            )
            centered_text(
                canvas,
                "Press Q or Esc to exit",
                int(height * 0.90),
                0.9,
                2,
                SECONDARY_TEXT_COLOR,
            )

            cv.imshow(WINDOW_NAME, canvas)
            key = cv.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if cv.getWindowProperty(WINDOW_NAME, cv.WND_PROP_VISIBLE) < 1:
                break
    finally:
        cv.destroyWindow(WINDOW_NAME)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Display the system clock as a Unix timestamp for camera-delay calibration."
    )
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    parser.add_argument("--windowed", action="store_true")
    args = parser.parse_args()
    run_calibration_clock(width=args.width, height=args.height, windowed=args.windowed)


if __name__ == "__main__":
    main()
