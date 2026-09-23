def parse_callback_id(data: str, prefix: str, maximum: int = 9223372036854775807) -> int:
    if not data.startswith(prefix):
        raise ValueError("Unexpected callback")
    raw = data[len(prefix) :]
    if not raw or not raw.isascii() or not raw.isdecimal():
        raise ValueError("Invalid callback ID")
    value = int(raw)
    if value <= 0 or value > maximum:
        raise ValueError("Callback ID out of range")
    return value
