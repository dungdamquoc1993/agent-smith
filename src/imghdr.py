"""Small Python 3.14 compatibility shim for deprecated stdlib imghdr.

The project pins a LiteLLM version that still imports ``imghdr.what``. Python
3.13 removed the stdlib module, so tests on newer interpreters need this
minimal equivalent.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO


def what(file: str | bytes | Path | BinaryIO | None, h: bytes | None = None) -> str | None:
    if h is None:
        if file is None:
            return None
        if hasattr(file, "read"):
            current = file.tell()
            h = file.read(32)
            file.seek(current)
        else:
            with open(file, "rb") as handle:
                h = handle.read(32)

    if h.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if h.startswith(b"BM"):
        return "bmp"
    if h.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if len(h) >= 12 and h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "webp"
    return None
