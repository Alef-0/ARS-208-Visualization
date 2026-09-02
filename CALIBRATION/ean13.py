"""EAN-13 generation and Pygame rendering for calibration timestamps."""

import pygame


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
    if len(payload) != EAN_PAYLOAD_DIGITS or not payload.isdigit():
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


def draw_ean13(
    surface: pygame.Surface,
    payload: str,
    area: pygame.Rect,
    *,
    dark_color: tuple[int, int, int] = (MIN_PIXEL_VALUE,) * 3,
    light_color: tuple[int, int, int] = (MAX_PIXEL_VALUE,) * 3,
) -> None:
    """Draw an EAN-13 barcode directly into a Pygame surface."""
    if not isinstance(area, pygame.Rect):
        area = pygame.Rect(area)
    if area.width <= 0 or area.height <= 0:
        raise ValueError("Barcode area must be positive")
    bits = ean13_bits(payload)
    quiet_left_modules = 11
    quiet_right_modules = 7
    total_modules = quiet_left_modules + len(bits) + quiet_right_modules
    module_width = area.width // total_modules
    if module_width < 1:
        raise ValueError("Barcode area is too narrow for one-pixel modules")
    barcode_width = total_modules * module_width
    offset_x = area.x + (area.width - barcode_width) // 2
    surface.fill(light_color, area)
    bars_x = offset_x + quiet_left_modules * module_width
    bar_rect = pygame.Rect(bars_x, area.y, module_width, area.height)
    for index, bit in enumerate(bits):
        if bit == "1":
            bar_rect.x = bars_x + index * module_width
            pygame.draw.rect(surface, dark_color, bar_rect)
