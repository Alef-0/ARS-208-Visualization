MISSING_QUALITY = 0xFFFFFFFF


def check_payload(payload: bytes) -> None:
    if len(payload) != 8:
        raise ValueError(f"Expected an 8-byte CAN payload, got {len(payload)}")
