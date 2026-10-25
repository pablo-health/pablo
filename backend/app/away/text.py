# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The words an away acknowledgment is made of.

Four parts, and only one is the clinician's:

* **The body** — what the clinician reviewed when they saved their away
  dates. :func:`default_body` writes the starting point; whatever they
  saved is sent as saved.
* **The cover line** — :func:`cover_line`, added after the body by
  :func:`with_cover` only while the colleague asked to cover has accepted.
  It is not part of the stored text: written in when the dates were set, it
  went on naming a colleague who had not answered, or had said no.
* **The safety lines** — :data:`SAFETY_LINES`, appended after the body
  every time. They are not part of the stored text, so no edit removes
  them.
* **For a refill request**, :func:`refill_line` stands in for the body:
  the request is in, and who will review it — the colleague covering once
  they accepted, otherwise the clinician by their return date.

Nothing clinical is ever added, and no time of day appears anywhere: the
only date promised is the return date the clinician set, shown to them in
the text before it was saved.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import date

EMERGENCY_LINE = "If this is an emergency, call 911."
CRISIS_LINE = "For the Suicide & Crisis Lifeline, call or text 988."

#: Appended after every acknowledgment, in this order.
SAFETY_LINES = f"{EMERGENCY_LINE}\n{CRISIS_LINE}"


def format_day(day: date) -> str:
    """``Monday, November 3`` — a weekday and a date, never a time."""
    return f"{day:%A}, {day:%B} {day.day}"


def default_body(
    return_date: date, clinician_name: str | None, _covering_name: str | None = None
) -> str:
    """The text a clinician is shown to review when they set away dates.

    It never names a covering colleague: :func:`with_cover` adds that when
    the acknowledgment is sent, if they accepted by then. The third argument
    is ignored and kept only so existing callers keep working; drop it once
    none pass it.
    """
    who = clinician_name or "Your clinician"
    return f"Thanks for your message. {who} is away and back on {format_day(return_date)}."


def cover_line(covering_name: str) -> str:
    """The sentence naming the colleague covering."""
    return f"{covering_name} is covering in the meantime."


def without_cover(body: str, covering_name: str | None) -> str:
    """*body* with the cover sentence taken out.

    Bodies saved before the cover line moved to send time carry it in the
    stored text. It is removed whenever the body is read, not rewritten in
    the table: that takes it out of what a client is sent and of what the
    clinician sees and saves next, and leaves everything they wrote
    themselves untouched.
    """
    body = body.strip()
    if not covering_name:
        return body
    sentence = cover_line(covering_name)
    if sentence not in body:
        return body
    kept = []
    for line in body.split("\n"):
        if sentence not in line:
            kept.append(line)
        elif rest := " ".join(line.replace(sentence, " ").split()):
            kept.append(rest)
    # A line that was only the sentence leaves no extra blank line behind.
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def with_cover(body: str, covering_name: str | None, *, accepted: bool) -> str:
    """The body as sent: the cover sentence only while the colleague has accepted."""
    body = without_cover(body, covering_name)
    if not covering_name or not accepted:
        return body
    return f"{body} {cover_line(covering_name)}".strip()


def compose(body: str) -> str:
    """The acknowledgment as sent: the reviewed body, then the safety lines."""
    return f"{body.strip()}\n\n{SAFETY_LINES}"


def refill_line(return_date: date, covering_name: str | None) -> str:
    """What a refill request is told while its clinician is away.

    Pass *covering_name* only for a colleague who accepted. They are then
    the one named, and the return date is left out.
    """
    if covering_name:
        return f"Your request is in. {covering_name} will review it."
    return f"Your request is in. It will be reviewed by {format_day(return_date)}."
