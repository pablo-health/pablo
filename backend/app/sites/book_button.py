# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The "Book a session" link a practice can show on its website.

A website is a folder the practice uploaded, so the link is added as each page
is served rather than written into the practice's files: a small link fixed to
the corner of every HTML page, just before ``</body>``. Turning it off, or the
booking link it points at being turned off, takes it off every page on the
next load, with nothing to re-upload (:mod:`app.portal.booking_button`).

It wears the accent of the website's ``theme.json`` when that gives one and the
text color to go on it — a pair already held to WCAG AA there — and a dark
button with white text otherwise. Its styles are inline and ``!important`` so
the page's own CSS cannot hide or restyle it, and it is a plain link: no
script.

No PHI: a public address and two colors.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..sites.theme import PracticeTheme

LABEL = "Book a session"
DEFAULT_BACKGROUND = "#1f2937"
DEFAULT_TEXT = "#ffffff"

_BODY_CLOSE = re.compile(rb"</body\s*>", re.IGNORECASE)


@dataclass(frozen=True)
class BookButton:
    """The link to add: where it goes, and its colors (checked hex)."""

    url: str
    background: str = DEFAULT_BACKGROUND
    color: str = DEFAULT_TEXT

    @property
    def tag(self) -> str:
        """A short name for this exact button, for the pages' ETag."""
        digest = hashlib.sha256(f"{self.url} {self.background} {self.color}".encode()).hexdigest()
        return digest[:12]


def book_button_for(url: str | None, theme: PracticeTheme | None) -> BookButton | None:
    """The button for *url* in *theme*'s colors, or ``None`` for no link."""
    if url is None:
        return None
    colors = theme.colors if theme is not None else None
    if colors is not None and colors.accent and colors.accent_text:
        return BookButton(url=url, background=colors.accent, color=colors.accent_text)
    return BookButton(url=url)


def _markup(button: BookButton) -> bytes:
    style = ";".join(
        f"{prop}:{value} !important"
        for prop, value in (
            ("position", "fixed"),
            ("right", "20px"),
            ("bottom", "20px"),
            ("z-index", "2147483000"),
            ("display", "inline-block"),
            ("padding", "12px 20px"),
            ("border-radius", "999px"),
            ("background", button.background),
            ("color", button.color),
            ("font", "600 16px/1.2 system-ui, -apple-system, 'Segoe UI', sans-serif"),
            ("text-decoration", "none"),
            ("box-shadow", "0 2px 10px rgba(0, 0, 0, 0.2)"),
        )
    )
    return (
        f'<a href="{escape(button.url, quote=True)}" data-pablo-book-session style="{style}">'
        f"{LABEL}</a>"
    ).encode()


def with_book_button(page: bytes, button: BookButton) -> bytes:
    """*page* with the link added just before its last ``</body>``, or at its end."""
    closes = list(_BODY_CLOSE.finditer(page))
    if not closes:
        return page + _markup(button)
    at = closes[-1].start()
    return page[:at] + _markup(button) + page[at:]
