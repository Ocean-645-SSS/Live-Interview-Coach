"""Filename decoding helpers for multipart transport and legacy metadata."""

from __future__ import annotations

import re
from urllib.parse import unquote


_LEGACY_UTF8_BYTES = re.compile(r"(?:_[0-9A-Fa-f]{2}){2,}")


def decode_transport_filename(value: str) -> str:
    """Restore UTF-8 names encoded by multipart clients or older sanitization."""

    decoded = unquote(str(value or ""))

    def restore(match: re.Match[str]) -> str:
        try:
            raw = bytes.fromhex(match.group(0).replace("_", ""))
            restored = raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return match.group(0)
        return restored

    return _LEGACY_UTF8_BYTES.sub(restore, decoded)
