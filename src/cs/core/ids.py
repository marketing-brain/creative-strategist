import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_seq = 0


def new_id() -> uuid.UUID:
    """UUIDv7: 48-bit unix ms, 12-bit sequence, 62 random bits. Monotonic per process."""
    global _last_ms, _seq
    with _lock:
        ms = time.time_ns() // 1_000_000
        if ms <= _last_ms:
            ms = _last_ms
            _seq += 1
            if _seq >= 0xFFF:
                ms += 1
                _seq = 0
        else:
            _seq = 0
        _last_ms = ms
        rand = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
        value = (ms << 80) | (0x7 << 76) | (_seq << 64) | (0b10 << 62) | rand
        return uuid.UUID(int=value)
