"""Reading an ``UploadFile`` into memory without trusting its size.

Starlette spools a large upload to disk, so ``await upload.read()`` with no
limit pulls the whole file into one bytes object before any size check can
run. Reading one byte past the cap is enough to know the upload is over it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import UploadFile


async def read_bounded(upload: UploadFile, max_bytes: int, *, too_large: Exception) -> bytes:
    """The upload's bytes, or ``too_large`` raised once it exceeds ``max_bytes``."""
    data = await upload.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise too_large
    return data
