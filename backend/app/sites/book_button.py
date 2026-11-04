# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The "Book a session" link on a practice's website.

A website is a folder the practice uploaded, so Pablo does not place the link:
the practice does, by marking a link of its own ``<a data-pablo="book">``,
wherever and however its pages want it. As each page is served, a marked link
is given the booking address the practice chose, or is taken out of the page
when the practice shows none — its own choice, or the booking link it points
at being turned off (:mod:`app.portal.booking_button`). Nothing to re-upload
either way, and a page with no marked link is served as it was uploaded.

Only the opening tag's ``href`` changes; the link's text, classes and other
attributes are the practice's. Removed, a link goes with its text, up to its
``</a>`` (links do not nest).

No PHI: a public address.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import escape

#: The marker, on an ``<a>`` of the practice's own.
MARK_ATTR = b"data-pablo"
MARK_VALUE = b"book"

_OPEN_A = re.compile(rb"""<a(?=[\s/>])(?:[^>"']|"[^"]*"|'[^']*')*>""", re.IGNORECASE)
_CLOSE_A = re.compile(rb"</a\s*>", re.IGNORECASE)
_ATTR = re.compile(rb"""[\s/]+([^\s"'>/=]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+))?""")


@dataclass(frozen=True)
class BookLink:
    """Where a website's marked link goes: *url*, or out of the page when ``None``."""

    url: str | None


def book_link_tag(url: str) -> str:
    """A short name for the pages served with their link going to *url*, for their ETag."""
    return hashlib.sha256(url.encode()).hexdigest()[:12]


def _attrs(tag: bytes) -> list[tuple[bytes, bytes | None, re.Match[bytes]]]:
    inner = tag[2:-1]
    return [
        (m.group(1).lower(), m.group(2).strip(b"\"'") if m.group(2) else None, m)
        for m in _ATTR.finditer(inner)
    ]


def _is_marked(tag: bytes) -> bool:
    return any(name == MARK_ATTR and value == MARK_VALUE for name, value, _ in _attrs(tag))


def _with_href(tag: bytes, url: str) -> bytes:
    """*tag* with its ``href`` set to *url*, added when it has none."""
    href = b' href="' + escape(url, quote=True).encode("ascii", "xmlcharrefreplace") + b'"'
    attrs = _attrs(tag)
    kept = b"".join(m.group(0) for name, _, m in attrs if name != b"href")
    tail = tag[2:-1][attrs[-1][2].end() :] if attrs else b""
    return b"<a" + href + kept + tail + b">"


def with_book_link(page: bytes, url: str | None) -> bytes:
    """*page* with each marked link going to *url*, or taken out when *url* is ``None``."""
    if MARK_ATTR not in page.lower():
        return page
    out: list[bytes] = []
    at = 0
    for opening in _OPEN_A.finditer(page):
        if opening.start() < at or not _is_marked(opening.group(0)):
            continue
        out.append(page[at : opening.start()])
        if url is not None:
            out.append(_with_href(opening.group(0), url))
            at = opening.end()
        else:
            closing = _CLOSE_A.search(page, opening.end())
            at = closing.end() if closing else opening.end()
    out.append(page[at:])
    return b"".join(out)
