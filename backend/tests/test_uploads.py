"""``read_bounded`` stops reading one byte past the cap."""

from __future__ import annotations

from io import BytesIO

import pytest
from app.uploads import read_bounded
from fastapi import HTTPException, UploadFile

MAX = 16


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _upload(payload: bytes) -> UploadFile:
    return UploadFile(BytesIO(payload), filename="sample.bin")


@pytest.mark.anyio
async def test_exactly_max_is_returned_whole() -> None:
    payload = b"x" * MAX
    assert await read_bounded(_upload(payload), MAX, too_large=ValueError("over")) == payload


@pytest.mark.anyio
async def test_one_past_max_raises_the_given_error() -> None:
    too_large = HTTPException(status_code=413, detail="too big")
    with pytest.raises(HTTPException) as excinfo:
        await read_bounded(_upload(b"x" * (MAX + 1)), MAX, too_large=too_large)
    assert excinfo.value is too_large


@pytest.mark.anyio
async def test_reads_no_more_than_one_byte_past_max() -> None:
    upload = _upload(b"x" * (MAX * 4))
    with pytest.raises(ValueError, match="over"):
        await read_bounded(upload, MAX, too_large=ValueError("over"))
    assert len(await upload.read()) == MAX * 4 - (MAX + 1)
