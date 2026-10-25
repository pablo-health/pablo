# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The words an away acknowledgment is made of.

Three parts, and only one is the clinician's:

* **The body** — what the clinician reviewed when they saved their away
  dates. :func:`default_body` writes the starting point; whatever they
  saved is sent as saved.
* **The safety lines** — :data:`SAFETY_LINES`, appended after the body
  every time. They are not part of the stored text, so no edit removes
  them.
* **For a refill request**, :func:`refill_line` stands in for the body:
  the request is in, and who will review it by when.

Nothing clinical is ever added, and no time of day appears anywhere: the
only date promised is the return date the clinician set, shown to them in
the text before it was saved.
"""

from __future__ import annotations

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


def default_body(return_date: date, clinician_name: str | None, covering_name: str | None) -> str:
    """The text a clinician is shown to review when they set away dates."""
    who = clinician_name or "Your clinician"
    lines = [f"Thanks for your message. {who} is away and back on {format_day(return_date)}."]
    if covering_name:
        lines.append(f"{covering_name} is covering in the meantime.")
    return " ".join(lines)


def compose(body: str) -> str:
    """The acknowledgment as sent: the reviewed body, then the safety lines."""
    return f"{body.strip()}\n\n{SAFETY_LINES}"


def refill_line(return_date: date, covering_name: str | None) -> str:
    """What a refill request is told while its clinician is away."""
    reviewer = format_day(return_date)
    if covering_name:
        reviewer = f"{reviewer} or by {covering_name}"
    return f"Your request is in. It will be reviewed by {reviewer}."
