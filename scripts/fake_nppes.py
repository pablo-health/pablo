# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in for the NPPES registry, for the end-to-end stack.

The real registry is a public CMS service with no key, which makes it tempting
to just call it from tests. Two reasons not to. It is somebody else's uptime,
so a suite that reaches it fails on their bad afternoon rather than ours. And
the records in it are real people's, which is not what a test fixture should
be — a name in an assertion here would be a real clinician's name in a public
repository.

So the stack points ``NPPES_BASE_URL`` at this instead. It answers the same
shapes the real one does, which is the part that matters: the ``Errors`` key
NPPES returns with an HTTP 200 for a bad request, a result set with an empty
``results`` list for a number nobody holds, and a full record for one that is
held. Those three are what ``app.credentialing.nppes`` branches on.

Fixtures live in ``backend/tests/fixtures/nppes`` so they can be read by a unit
test as easily as served from here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="fake-nppes")

FIXTURES = Path(os.environ.get("FAKE_NPPES_FIXTURES", "/srv/fixtures"))


def _records() -> dict[str, Any]:
    """Every fixture record, keyed by NPI."""
    path = FIXTURES / "records.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _empty() -> dict[str, Any]:
    return {"result_count": 0, "results": []}


@app.get("/")
async def lookup(request: Request) -> Any:
    """Answer the one endpoint NPPES exposes, in the shapes it really uses."""
    params = request.query_params

    if params.get("version") != "2.1":
        # The real registry reports this in the body with a 200, which is
        # exactly the shape the caller has to handle, so the fake does too.
        return JSONResponse(
            {"Errors": [{"description": "Version is required", "field": "version"}]}
        )

    records = _records()
    number = (params.get("number") or "").strip()

    if number:
        record = records.get(number)
        if record is None:
            return _empty()
        return {"result_count": 1, "results": [record]}

    last_name = (params.get("last_name") or "").strip().upper()
    if not last_name:
        return JSONResponse({"Errors": [{"description": "A search criterion is required"}]})

    first_name = (params.get("first_name") or "").strip().upper()
    state = (params.get("state") or "").strip().upper()

    def matches(record: dict[str, Any]) -> bool:
        basic = record.get("basic") or {}
        if str(basic.get("last_name", "")).upper() != last_name:
            return False
        if first_name and str(basic.get("first_name", "")).upper() != first_name:
            return False
        if state:
            states = {
                str(a.get("state", "")).upper()
                for a in (record.get("addresses") or [])
                if isinstance(a, dict)
            }
            if state not in states:
                return False
        return True

    found = [r for r in records.values() if matches(r)]
    return {"result_count": len(found), "results": found}


@app.get("/_fake/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
