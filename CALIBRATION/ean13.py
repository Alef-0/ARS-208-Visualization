"""EAN-13 generation and Pygame rendering for calibration timestamps."""

import pygame
from functools import lru_cache


EAN_PAYLOAD_DIGITS = 12
EAN_MODULUS_MS = 10**EAN_PAYLOAD_DIGITS
MIN_PIXEL_VALUE = 50
MAX_PIXEL_VALUE = 200

EAN_L_PATTERNS = (
    "0001101", "0011001", "0010011", "0111101", "0100011",
    "0110001", "0101111", "0111011", "0110111", "0001011",
)
EAN_G_PATTERNS = (
    "0100111", "0110011", "0011011", "0100001", "0011101",
    "0111001", "0000101", "0010001", "0001001", "0010111",
)
EAN_R_PATTERNS = (
    "1110010", "1100110", "1101100", "1000010", "1011100",
    "1001110", "1010000", "1000100", "1001000", "1110100",
)
EAN_LEFT_PARITY = (
    "LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG",
    "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL",
)


def ean13_check_digit(payload: str) -> str:
    """Return the EAN-13 check digit for a 12-digit payload."""
    if len(payload) != EAN_PAYLOAD_DIGITS or not payload.isascii() or not payload.isdigit():
        raise ValueError("EAN-13 payload must contain exactly 12 digits")
    weighted_sum = sum(int(value) for value in payload[::2])
    weighted_sum += 3 * sum(int(value) for value in payload[1::2])
    return str((-weighted_sum) % 10)


def monotonic_ms_payload(monotonic_ns: int) -> str:
    """Encode monotonic milliseconds into the 12 EAN-13 payload digits."""
    monotonic_ms = monotonic_ns // 1_000_000
    return f"{monotonic_ms % EAN_MODULUS_MS:0{EAN_PAYLOAD_DIGITS}d}"


def ean13_bits(payload: str) -> str:
    """Encode a 12-digit payload as the 95 EAN-13 bar modules."""
    check_digit = ean13_check_digit(payload)
    digits = payload + check_digit
    parity = EAN_LEFT_PARITY[int(digits[0])]
    left = "".join(
        (EAN_L_PATTERNS if mode == "L" else EAN_G_PATTERNS)[int(digit)]
        for mode, digit in zip(parity, digits[1:7])
    )
    right = "".join(EAN_R_PATTERNS[int(digit)] for digit in digits[7:])
    return "101" + left + "01010" + right + "101"


def _dark_runs(pattern: str) -> tuple[tuple[int, int], ...]:
    """Cache contiguous bars, rather than drawing each dark module separately."""
    runs = []
    start = None
    for index, bit in enumerate(pattern + "0"):
        if bit == "1" and start is None:
            start = index
        elif bit == "0" and start is not None:
            runs.append((start, index - start))
            start = None
    return tuple(runs)


class EAN13Painter:
    """Precomputed pixel rectangles for one fixed-size barcode panel."""

    def __init__(self, area):
        self.area = pygame.Rect(area)
        if self.area.width <= 0 or self.area.height <= 0:
            raise ValueError("Barcode area must be positive")
        module_width = self.area.width // 113  # 11 quiet + 95 encoded + 7 quiet
        if module_width < 1:
            raise ValueError("Barcode area is too narrow for one-pixel modules")
        bars_x = self.area.x + (self.area.width - 113 * module_width) // 2 + 11 * module_width
        self.bars = pygame.Rect(bars_x, self.area.y, 95 * module_width, self.area.height)

        def rectangles(pattern, offset):
            return tuple(
                pygame.Rect(bars_x + (offset + start) * module_width,
                            self.area.y, length * module_width, self.area.height)
                for start, length in _dark_runs(pattern)
            )

        self._guards = tuple(
            rect for pattern, offset in (("101", 0), ("01010", 45), ("101", 92))
            for rect in rectangles(pattern, offset)
        )
        self._left = tuple({
            mode: tuple(rectangles(pattern, 3 + position * 7) for pattern in patterns)
            for mode, patterns in (("L", EAN_L_PATTERNS), ("G", EAN_G_PATTERNS))
        } for position in range(6))
        self._right = tuple(
            tuple(rectangles(pattern, 50 + position * 7) for pattern in EAN_R_PATTERNS)
            for position in range(6)
        )

    def draw(self, surface, payload, dark_color=(50, 50, 50), light_color=(200, 200, 200)):
        digits = payload + ean13_check_digit(payload)
        parity = EAN_LEFT_PARITY[int(digits[0])]
        surface.fill(light_color, self.area)
        surface.lock()
        try:
            for rect in self._guards:
                surface.fill(dark_color, rect)
            for position, mode in enumerate(parity):
                for rect in self._left[position][mode][int(digits[position + 1])]:
                    surface.fill(dark_color, rect)
            for position in range(6):
                for rect in self._right[position][int(digits[position + 7])]:
                    surface.fill(dark_color, rect)
        finally:
            surface.unlock()


@lru_cache(maxsize=16)
def _painter(area: tuple[int, int, int, int]) -> EAN13Painter:
    return EAN13Painter(area)


def draw_ean13(
    surface: pygame.Surface,
    payload: str,
    area: pygame.Rect,
    *,
    dark_color: tuple[int, int, int] = (MIN_PIXEL_VALUE,) * 3,
    light_color: tuple[int, int, int] = (MAX_PIXEL_VALUE,) * 3,
) -> None:
    """Draw an EAN-13 barcode directly into a Pygame surface."""
    _painter(tuple(area)).draw(surface, payload, dark_color, light_color)
