from __future__ import annotations

from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def strip_access_tokens(value: Any) -> Any:
    """Recursively drop `access_token` query parameters from strings in a payload.

    Meta echoes the caller's token back inside `paging.next`/`paging.previous` URLs, so
    anything we persist or hand to a fixture must be walked and scrubbed first.
    """
    if isinstance(value, dict):
        return {k: strip_access_tokens(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_access_tokens(v) for v in value]
    if isinstance(value, str) and "access_token=" in value:
        parts = urlsplit(value)
        query = [
            (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "access_token"
        ]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    return value
