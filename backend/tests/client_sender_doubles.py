# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Capturing channels that can send as the practice, for the call-site tests.

Each records, beside what it sent, who it was bound to send as when it sent
it. Binding returns a copy that shares the log, the way a real channel's copy
shares its transport.
"""

from __future__ import annotations

import copy
from typing import Self

from app.portal.delivery import (
    CapturingNoticeDelivery,
    CapturingRenderedInviteDelivery,
    ClientSender,
)

#: An example practice sender. Example domains only.
EXAMPLE_SENDER = ClientSender(
    from_name="Jordan Rivera, LCSW",
    from_address="portal@example.com",
    reply_to="frontdesk@example.com",
)


class SenderAwareInviteDelivery(CapturingRenderedInviteDelivery):
    def __init__(self) -> None:
        super().__init__()
        self.sender: ClientSender | None = None
        #: Who each invitation in ``sent`` went out as, in the same order.
        self.sent_as: list[ClientSender | None] = []

    def sending_as(self, sender: ClientSender) -> Self:
        bound = copy.copy(self)
        bound.sender = sender
        return bound

    def send_rendered_invite(self, *, to_email: str, subject: str, text: str) -> None:
        super().send_rendered_invite(to_email=to_email, subject=subject, text=text)
        self.sent_as.append(self.sender)

    def send_invite(self, *, to_email: str, link: str) -> None:
        super().send_invite(to_email=to_email, link=link)
        self.sent_as.append(self.sender)


class SenderAwareNoticeDelivery(CapturingNoticeDelivery):
    def __init__(self) -> None:
        super().__init__()
        self.sender: ClientSender | None = None
        self.sent_as: list[ClientSender | None] = []

    def sending_as(self, sender: ClientSender) -> Self:
        bound = copy.copy(self)
        bound.sender = sender
        return bound

    def send_notice(self, *, to_email: str, notice: str, link: str) -> None:
        super().send_notice(to_email=to_email, notice=notice, link=link)
        self.sent_as.append(self.sender)
