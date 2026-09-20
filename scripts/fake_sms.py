# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A stand-in text-message gateway for the end-to-end stack.

The portal's step-up factor is a six-digit code delivered to a phone, which a
browser test cannot read — and a test that cannot observe the second factor
cannot walk a sign-in at all. So the stack runs this: the backend's
``capture`` gateway (``app.portal.adapters.CapturingSmsGateway``, selected
with ``PORTAL_SMS_GATEWAY=capture``) posts every message here instead of
sending it, and a spec reads it back over HTTP.

The same shape as the other fakes in this stack: the product talks to a
configured origin, what listens there records instead of delivering, and the
test hooks live under ``/_fake``. ``POST /_fake/sms`` is what the gateway
calls; ``GET /_fake/messages`` lists what has arrived since the last reset,
oldest first, and ``POST /_fake/reset`` clears the list.

This process hands its caller a working credential, which is exactly why it
belongs to the local stack and nowhere else. The gateway that feeds it is
refused outside a development environment (see
``app.portal.factory.sms_gateway_from_settings``).

Run locally with ``uvicorn scripts.fake_sms:app --port 8026``; the compose
stack builds it from ``scripts/e2e/fake-sms.Dockerfile``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel


class OutboundSms(BaseModel):
    """What the gateway posts: a recipient and a body, and nothing else."""

    to: str
    body: str


class Inbox:
    """Everything received since the last reset, oldest first."""

    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    def add(self, message: OutboundSms) -> None:
        self.messages.append(
            {
                "at": datetime.now(tz=UTC).isoformat(),
                "to": message.to,
                "body": message.body,
            }
        )

    def reset(self) -> None:
        self.messages.clear()


inbox = Inbox()

app = FastAPI(title="fake sms", docs_url=None, redoc_url=None)


@app.post("/_fake/sms", status_code=204)
async def receive(message: OutboundSms) -> None:
    inbox.add(message)


@app.get("/_fake/messages")
async def messages() -> Any:
    return {"messages": inbox.messages}


@app.post("/_fake/reset")
async def reset() -> Any:
    inbox.reset()
    return {"ok": True}
