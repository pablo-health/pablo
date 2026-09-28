# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The welcome a client reads on the portal's home screen, as a practice writes it.

A heading and a plain-text message, with one placeholder: ``{practice_name}``.
This module is the one place that knows the default, checks a practice's text
and fills it in — for the settings screen AND for the capability document the
portal reads, so the two cannot differ.

**Plain text only.** No markup and nothing interpreted: what a clinician types
is what the client reads, angle brackets and all.

**No client placeholder, on purpose.** The welcome travels in the capability
document, which a single-factor caller may read and which carries nothing
about the person asking. A link that reached the wrong inbox must not be able
to learn whose it was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PRACTICE_NAME_PLACEHOLDER = "practice_name"

#: Placeholder name -> what a clinician sees it called.
PLACEHOLDERS: dict[str, str] = {PRACTICE_NAME_PLACEHOLDER: "Practice name"}

MAX_HEADING_LENGTH = 120
MAX_BODY_LENGTH = 1000

DEFAULT_HEADING = "Welcome to {practice_name}"
DEFAULT_BODY = (
    "This is where you'll find what {practice_name} has asked you to do, and where "
    "you can reach them between visits. You can leave and come back any time using "
    "the link in your email."
)

#: What ``{practice_name}`` becomes for a practice with no display name yet.
UNNAMED_PRACTICE = "your practice"

#: A run of braces around a name is one token, so ``{{practice_name}}`` — the
#: invitation email's syntax, on the same settings page — fills in here too
#: rather than leaving a stray pair of braces behind.
_PLACEHOLDER_RE = re.compile(r"\{+\s*([a-zA-Z0-9_]+)\s*\}+")


@dataclass(frozen=True)
class PortalWelcome:
    heading: str
    body: str


DEFAULT_WELCOME = PortalWelcome(heading=DEFAULT_HEADING, body=DEFAULT_BODY)


def welcome_problems(welcome: PortalWelcome) -> list[str]:
    """What stops this welcome being saved, in words a clinician can act on.

    Empty when it is fine.
    """
    problems: list[str] = []
    heading = welcome.heading.strip()
    body = welcome.body.strip()
    if not heading:
        problems.append("Add a heading.")
    elif len(heading) > MAX_HEADING_LENGTH:
        problems.append(f"Keep the heading under {MAX_HEADING_LENGTH} characters.")
    if "\n" in heading:
        problems.append("Keep the heading on one line.")
    if not body:
        problems.append("Add a message.")
    elif len(body) > MAX_BODY_LENGTH:
        problems.append(f"Keep the message under {MAX_BODY_LENGTH} characters.")

    unknown = sorted({name for name in _names(heading) + _names(body) if name not in PLACEHOLDERS})
    if unknown:
        problems.append(
            "Remove the placeholders the welcome cannot fill in: "
            + ", ".join(f"{{{name}}}" for name in unknown)
            + "."
        )
    return problems


def render_welcome(welcome: PortalWelcome, practice_name: str | None) -> PortalWelcome:
    """Fill the practice's name in. The welcome is assumed to have passed
    :func:`welcome_problems`; an unknown placeholder is left as typed."""
    name = (practice_name or "").strip() or UNNAMED_PRACTICE

    def fill(text: str) -> str:
        return _PLACEHOLDER_RE.sub(
            lambda m: name if m.group(1) == PRACTICE_NAME_PLACEHOLDER else m.group(0), text
        )

    return PortalWelcome(heading=fill(welcome.heading.strip()), body=fill(welcome.body.strip()))


def _names(text: str) -> list[str]:
    return _PLACEHOLDER_RE.findall(text)
