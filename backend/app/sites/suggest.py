# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A portal header suggested from a website's ``index.html``, for a website
whose ``theme.json`` declares none (:mod:`app.sites.header`).

Run once, when a draft is saved. The practice sees the suggestion in Settings
and accepts or edits it; until then it is only a suggestion, and nothing of it
reaches the portal.

**Exact, when the author says.** An element marked ``data-pablo-header``, with
children marked ``data-pablo="brand"``, ``"subtitle"``, ``"nav"`` and
``"cta"``, gives exactly those values: the brand's text is the wordmark, every
link in (or marked) ``nav`` is a link, and the ``cta`` link is the call to
action. (``data-pablo="logo"`` is reserved for when the portal shows a logo.)

**Guessed, otherwise.** In the page's first ``<header>``, else its first
``<nav>``: the link to the home page, else the first link with the most text,
is the brand. When the brand holds two pieces of text the first is the
wordmark and the second the subtitle; otherwise a short piece of text beside
it is the subtitle. A link styled as a button (a class with ``btn`` or
``button`` in it) is the call to action, or else the last link. The rest, in
the order they appear, are the links. Links to the practice's portal itself
are left out: from the portal they go nowhere new.

Either way, a link relative to the page (``#services``, ``about.html``) is
made a path on the website (``/#services``, ``/about.html``), since
``index.html`` is the home page, and every value then goes through the same
checks as a header declared in ``theme.json``, so a suggestion is never looser
than a declaration. Values that fail are dropped with their reasons.

Pure: no network and no files, the standard library's HTML parser over at most
:data:`MAX_INDEX_BYTES`; a larger page is not read at all.

No PHI: a practice's public website.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict

from .header import PracticeHeader, read_header
from .theme import SkippedValue

if TYPE_CHECKING:
    from collections.abc import Iterator

#: The most of ``index.html`` read for a suggestion.
MAX_INDEX_BYTES = 256 * 1024

_VOID = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "source",
        "track",
        "wbr",
    }
)
_SKIPPED_CONTENT = frozenset({"script", "style", "template", "noscript", "svg"})
_HOME_HREFS = frozenset({"/", "#", "#top", "./", "index.html", "/index.html"})
#: A subtitle beside the brand is short; anything longer is not one.
_SUBTITLE_MAX = 80


class HeaderSuggestion(BaseModel):
    """A suggested header: what passed, and what was found but failed and why."""

    model_config = ConfigDict(frozen=True)

    header: PracticeHeader | None
    skipped: list[SkippedValue] = []


@dataclass
class _Element:
    tag: str
    attrs: dict[str, str]
    children: list[_Element | str] = field(default_factory=list)

    def walk(self) -> Iterator[_Element]:
        yield self
        for child in self.children:
            if isinstance(child, _Element):
                yield from child.walk()

    def text(self) -> str:
        return " ".join(
            part
            for child in self.children
            for part in [child if isinstance(child, str) else child.text()]
            if part.strip()
        ).strip()

    def first(self, tag: str) -> _Element | None:
        return next((e for e in self.walk() if e.tag == tag), None)

    def links(self) -> list[_Element]:
        return [e for e in self.walk() if e.tag == "a" and "href" in e.attrs]


class _TreeBuilder(HTMLParser):
    """The page as nested elements, forgiving of HTML as browsers are: a stray
    end tag closes back to its opener, and one with no opener is ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("#document", {})
        self._open = [self.root]
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skipping:
            self._skipping += tag in _SKIPPED_CONTENT
            return
        if tag in _SKIPPED_CONTENT:
            self._skipping = 1
            return
        element = _Element(tag, {k: v or "" for k, v in attrs})
        self._open[-1].children.append(element)
        if tag not in _VOID:
            self._open.append(element)

    def handle_endtag(self, tag: str) -> None:
        if self._skipping:
            self._skipping -= tag in _SKIPPED_CONTENT
            return
        for i in range(len(self._open) - 1, 0, -1):
            if self._open[i].tag == tag:
                del self._open[i:]
                return

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self._open[-1].children.append(data)


def _page(index_html: bytes) -> _Element:
    builder = _TreeBuilder()
    builder.feed(index_html.decode("utf-8", errors="replace"))
    builder.close()
    return builder.root


def _href(link: _Element) -> str:
    """The link as the header holds it: relative to the home page made a path."""
    href = link.attrs.get("href", "").strip()
    if href.startswith("/") or urlsplit(href).scheme:
        return href
    return urljoin("/", href)


def _is_button(link: _Element) -> bool:
    classes = link.attrs.get("class", "").lower().split()
    return any("btn" in c or "button" in c for c in classes)


def _text_blocks(element: _Element) -> list[str]:
    """The separate pieces of text in *element*: its child elements' text, or
    its own when it has no child elements."""
    blocks = [c.text() for c in element.children if isinstance(c, _Element)]
    blocks = [b for b in blocks if b]
    return blocks or ([element.text()] if element.text() else [])


def _subtitle_beside(container: _Element, brand: _Element) -> str | None:
    """A short piece of text right after the brand, in the same parent."""
    for parent in container.walk():
        if brand not in parent.children:
            continue
        after = parent.children[parent.children.index(brand) + 1 :]
        for sibling in after:
            if isinstance(sibling, _Element):
                text = sibling.text()
                if sibling.links() or len(text) > _SUBTITLE_MAX:
                    return None
                if text:
                    return text
            elif sibling.strip():
                return sibling.strip()
        return None
    return None


def _link(link: _Element) -> dict[str, str]:
    return {"label": link.text(), "href": _href(link)}


def _on_portal(link: _Element, portal_hosts: frozenset[str]) -> bool:
    host = (urlsplit(_href(link)).hostname or "").removesuffix(".")
    return host in portal_hosts


def _marked(page: _Element) -> dict[str, object] | None:
    root = next((e for e in page.walk() if "data-pablo-header" in e.attrs), None)
    if root is None:
        return None
    raw: dict[str, object] = {}
    links: list[dict[str, str]] = []
    for element in root.walk():
        role = element.attrs.get("data-pablo")
        if role == "brand" and "wordmark" not in raw:
            raw["wordmark"] = element.text()
        elif role == "subtitle" and "subtitle" not in raw:
            raw["subtitle"] = element.text()
        elif role == "nav":
            members = [element] if element.tag == "a" else element.links()
            links.extend(_link(a) for a in members if "href" in a.attrs)
        elif role == "cta" and "cta" not in raw:
            targets = [element] if element.tag == "a" else element.links()
            if targets:
                raw["cta"] = _link(targets[0])
    if links:
        raw["links"] = links
    return raw


def _guessed(page: _Element, portal_hosts: frozenset[str]) -> dict[str, object] | None:
    container = page.first("header") or page.first("nav")
    if container is None:
        return None
    links = [a for a in container.links() if not _on_portal(a, portal_hosts)]
    if not links:
        return None
    brand = next((a for a in links if a.attrs["href"].strip() in _HOME_HREFS), None)
    if brand is None:
        brand = max(links, key=lambda a: len(a.text()))
    raw: dict[str, object] = {}
    blocks = _text_blocks(brand)
    if blocks:
        raw["wordmark"] = blocks[0]
    subtitle = blocks[1] if len(blocks) > 1 else _subtitle_beside(container, brand)
    if subtitle:
        raw["subtitle"] = subtitle
    rest = [a for a in links if a is not brand]
    cta = next((a for a in rest if _is_button(a)), None)
    if cta is None and len(rest) > 1:
        cta = rest[-1]
    if cta is not None:
        raw["cta"] = _link(cta)
    nav = [_link(a) for a in rest if a is not cta]
    if nav:
        raw["links"] = nav
    return raw


def suggest_header(
    index_html: bytes,
    hosts: frozenset[str],
    portal_hosts: frozenset[str] = frozenset(),
    brands: tuple[str, ...] | None = None,
) -> HeaderSuggestion | None:
    """A header suggested from *index_html*, or ``None`` when the page is too
    large or has no header or navigation to take one from.

    *hosts* are the practice's own, as for a declared header; *portal_hosts*
    those of them its portal is served on, whose links are left out.
    """
    if len(index_html) > MAX_INDEX_BYTES:
        return None
    page = _page(index_html)
    raw = _marked(page) or _guessed(page, portal_hosts)
    if not raw:
        return None
    skipped: list[SkippedValue] = []
    header = read_header(
        raw,
        hosts,
        lambda where, reason: skipped.append(SkippedValue(field=where, reason=reason)),
        brands,
    )
    return HeaderSuggestion(header=header, skipped=skipped)
