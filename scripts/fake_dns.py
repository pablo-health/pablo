# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in DNS server for the end-to-end stack.

Settings > Domains checks a practice's records by asking DNS, and a browser
test can neither publish a record on the real internet nor wait for one to
propagate. So the stack runs this: a name server that answers from a table,
which the backend asks instead of the system resolver
(``PRACTICE_DOMAIN_DNS_NAMESERVERS``), and which a spec fills over HTTP.

The same shape as the other fakes in this stack: the product talks to a
configured address, what listens there answers from fixtures, and the test
hooks live under ``/_fake``. ``PUT /_fake/records`` sets the values for one
name and type (an empty list removes them), ``GET /_fake/records`` lists the
table and ``POST /_fake/reset`` clears it.

DNS is plain UDP on ``FAKE_DNS_PORT`` (5353 by default, so no privilege is
needed). A name with records answers them; a name with a CNAME answers the
CNAME, and the target's records when the table has them; a name the table
knows with nothing of the asked type answers empty; any other name is
NXDOMAIN.

Run locally with ``uvicorn scripts.fake_dns:app --port 8027``; the compose
stack builds it from ``scripts/e2e/fake-dns.Dockerfile``.
"""

from __future__ import annotations

import os
import socketserver
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import dns.message
import dns.rcode
import dns.rdatatype
import dns.rrset
from fastapi import FastAPI
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_TTL = 60
#: How many CNAMEs an answer follows inside the table.
_MAX_CHAIN = 8


def _name(value: str) -> str:
    return value.strip().lower().rstrip(".")


class Zone:
    """Every record the fake answers, by (name, type)."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], list[str]] = {}
        self._lock = threading.Lock()

    def set(self, name: str, rdtype: str, values: list[str]) -> None:
        key = (_name(name), rdtype.upper())
        with self._lock:
            if values:
                self._records[key] = list(values)
            else:
                self._records.pop(key, None)

    def get(self, name: str, rdtype: str) -> list[str]:
        with self._lock:
            return list(self._records.get((_name(name), rdtype.upper()), []))

    def knows(self, name: str) -> bool:
        with self._lock:
            return any(key[0] == _name(name) for key in self._records)

    def all(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {"name": name, "type": rdtype, "values": values}
                for (name, rdtype), values in sorted(self._records.items())
            ]

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


def _rrset(name: str, rdtype: str, values: list[str]) -> dns.rrset.RRset:
    if rdtype == "TXT":
        texts = ['"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"' for v in values]
    elif rdtype == "CNAME":
        texts = [_name(v) + "." for v in values]
    else:
        texts = values
    return dns.rrset.from_text(_name(name) + ".", _TTL, "IN", rdtype, *texts)


def answer(zone: Zone, wire: bytes) -> bytes:
    """The response to one query, in wire format."""
    query = dns.message.from_wire(wire)
    response = dns.message.make_response(query)
    if not query.question:
        response.set_rcode(dns.rcode.FORMERR)
        return response.to_wire()
    question = query.question[0]
    name = _name(question.name.to_text())
    rdtype = dns.rdatatype.to_text(question.rdtype)

    for _ in range(_MAX_CHAIN):
        values = zone.get(name, rdtype)
        if values:
            response.answer.append(_rrset(name, rdtype, values))
            break
        cname = zone.get(name, "CNAME") if rdtype != "CNAME" else []
        if not cname:
            if not response.answer and not zone.knows(name):
                response.set_rcode(dns.rcode.NXDOMAIN)
            break
        response.answer.append(_rrset(name, "CNAME", cname[:1]))
        name = _name(cname[0])
    return response.to_wire()


zone = Zone()


class _Handler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        data, sock = self.request
        try:
            reply = answer(zone, data)
        except Exception:  # noqa: BLE001 — a malformed packet gets no answer, as on any server
            return
        sock.sendto(reply, self.client_address)


def serve_udp(host: str, port: int) -> socketserver.ThreadingUDPServer:
    """Start answering on (host, port) in a background thread."""
    server = socketserver.ThreadingUDPServer((host, port), _Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    server = serve_udp("0.0.0.0", int(os.environ.get("FAKE_DNS_PORT", "5353")))  # noqa: S104 — a container's only interface
    try:
        yield
    finally:
        server.shutdown()


app = FastAPI(title="fake dns", docs_url=None, redoc_url=None, lifespan=_lifespan)


class RecordSet(BaseModel):
    name: str
    type: str
    values: list[str]


@app.put("/_fake/records")
async def put_records(records: RecordSet) -> Any:
    zone.set(records.name, records.type, records.values)
    return {"ok": True}


@app.get("/_fake/records")
async def list_records() -> Any:
    return {"records": zone.all()}


@app.post("/_fake/reset")
async def reset() -> Any:
    zone.reset()
    return {"ok": True}


@app.get("/_fake/health")
async def health() -> Any:
    return {"ok": True}
