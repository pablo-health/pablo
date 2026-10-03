# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The ``header`` block of a website's ``theme.json``: how the portal's header
matches the website's.

::

    "header": {
      "wordmark": "Riverside Counseling",
      "subtitle": "Individual and couples therapy",
      "links": [{"label": "Services", "href": "/#services"},
                {"label": "About", "href": "/about"}],
      "cta": {"label": "Schedule a visit", "href": "/#schedule"}
    }

Every value is judged on its own, like the rest of ``theme.json``: one that
fails is skipped with a reason and the rest still apply.

**Text** (``wordmark``, ``subtitle``, each link's and the call to action's
``label``) is normalised (NFKC, runs of spaces made one, trimmed) and then
refused when it holds a control, formatting or invisible character (bidi
controls among them), a private-use or unassigned code point, or a line break;
when one word mixes alphabets (the trick behind look-alike names); when it is
empty; or when it is longer than its cap, counted in characters after
normalising. A label may not be the portal's own words ("Sign in" and the
like), and neither a label nor the wordmark may be the name of the software
the portal runs on (the ``portal_header_brand_names`` setting), so a website
can't make the portal look like someone else's sign-in page.

**Links** (each link's and the call to action's ``href``) are either a path on
the website — one ``/``, then the path, with an optional query and fragment,
never ``//`` or a ``..`` segment — or an ``https://`` address on one of the
practice's own hosts, with no user name and no port. Nothing else: no other
scheme, no other host. The same checks run again on the value percent-decoded
once, so an encoded ``..`` or ``//`` is caught too. A path is kept as written
and becomes an address on the live website when the portal shows it; a full
address is checked again whenever the portal is served (:func:`for_hosts`), so
one on a host the practice no longer holds disappears.

Nothing here is ever written into CSS or HTML: the portal renders these values
as text and link targets only, after checking them again itself
(``frontend/src/lib/portal-host/practice-header.ts``).

No PHI: a practice's public website header.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlsplit

from pydantic import BaseModel, ConfigDict

from ..settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

#: Caps, in characters after normalising.
WORDMARK_MAX = 60
SUBTITLE_MAX = 80
LABEL_MAX = 24
HREF_MAX = 512
MAX_LINKS = 5

#: Labels the portal uses itself, compared without case.
RESERVED_LABELS = frozenset({"sign in", "log in", "login", "sign out"})

#: Categories never allowed in header text: controls, formatting characters
#: (every bidi control is one), surrogates, private use, unassigned, and line
#: and paragraph separators.
_REFUSED_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

#: Scripts that are written together in one word: Japanese, Korean and
#: Chinese with phonetic annotation. Keys are the first word of a Unicode
#: character name (``CJK UNIFIED IDEOGRAPH-4E00`` is ``CJK``).
_WRITTEN_TOGETHER = (
    frozenset({"CJK", "HIRAGANA", "KATAKANA"}),
    frozenset({"CJK", "HANGUL"}),
    frozenset({"CJK", "BOPOMOFO"}),
)

_NOT_TEXT = "Should be text."
_INVISIBLE = "Contains a control or invisible character."
_MIXED = "Mixes alphabets in a way browsers warn about."
_EMPTY = "Is empty."
_RESERVED = "Is a word the portal uses itself."
_BRAND = "Can't be the name of the software the portal runs on."
_NOT_A_PAGE = "Must be a page on your website, like /about."
_TOO_MANY = f"The portal shows at most {MAX_LINKS} links."
_INCOMPLETE = "Needs a label and an href."
_DUPLICATE = "Goes to the same page as an earlier link."


class HeaderLink(BaseModel):
    model_config = ConfigDict(frozen=True)

    label: str
    #: A path on the website (``/about``) or an ``https://`` address on one of
    #: the practice's hosts.
    href: str


class PracticeHeader(BaseModel):
    """A header block as it is stored and served: only values that passed."""

    model_config = ConfigDict(frozen=True)

    wordmark: str | None = None
    subtitle: str | None = None
    links: list[HeaderLink] = []
    cta: HeaderLink | None = None


type Skip = Callable[[str, str], None]


def brand_names() -> tuple[str, ...]:
    """The names a header may not take: the deployment's own (``portal_header_brand_names``)."""
    return get_settings().portal_header_brand_name_list


def _script(char: str) -> str | None:
    """The script a letter is written in, approximated by the first word of its
    Unicode name; ``None`` for anything shared between scripts (digits,
    punctuation, marks, modifier letters)."""
    if unicodedata.category(char) not in {"Lu", "Ll", "Lt", "Lo"}:
        return None
    name = unicodedata.name(char, "")
    return name.split(" ", 1)[0] if name else "UNKNOWN"


def _mixes_scripts(text: str) -> bool:
    for word in text.split(" "):
        scripts = {s for s in map(_script, word) if s is not None}
        if len(scripts) > 1 and not any(scripts <= group for group in _WRITTEN_TOGETHER):
            return True
    return False


def _folded(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).casefold()


def read_text(raw: object, cap: int) -> str | tuple[None, str]:
    """*raw* normalised, or ``(None, reason)`` when it can't be used."""
    if not isinstance(raw, str):
        return None, _NOT_TEXT
    text = unicodedata.normalize("NFKC", raw)
    if any(unicodedata.category(c) in _REFUSED_CATEGORIES for c in text):
        return None, _INVISIBLE
    text = " ".join(text.split())
    if not text:
        return None, _EMPTY
    if len(text) > cap:
        return None, f"Is longer than {cap} characters."
    if _mixes_scripts(text):
        return None, _MIXED
    return text


def _shaped_like_a_page(href: str, hosts: frozenset[str], *, decoded: bool = False) -> bool:
    """Whether *href* is a path on the website or an address on *hosts*. Once
    *decoded*, a plain space (``%20``) is part of a path like any letter."""
    spaces_allowed = " " if decoded else ""
    if any(
        c not in spaces_allowed and (c.isspace() or c == "\\" or unicodedata.category(c)[0] in "CZ")
        for c in href
    ):
        return False
    if href.startswith("/"):
        return not href.startswith("//") and not _climbs(href.split("?", 1)[0].split("#", 1)[0])
    return href[:8].lower() == "https://" and _on_own_host(href, hosts)


def _climbs(path: str) -> bool:
    return ".." in path.split("/")


def _on_own_host(href: str, hosts: frozenset[str]) -> bool:
    """Whether the ``https://`` address *href* is on one of *hosts*, with no
    user name, port or ``..``."""
    try:
        parts = urlsplit(href)
    except ValueError:
        return False
    netloc = parts.netloc
    if not netloc or "@" in netloc or ":" in netloc or _climbs(parts.path):
        return False
    return netloc.lower().removesuffix(".") in hosts


def read_href(raw: object, hosts: frozenset[str]) -> str | tuple[None, str]:
    """*raw* as a link the header may carry, or ``(None, reason)``."""
    if not isinstance(raw, str):
        return None, _NOT_A_PAGE
    if len(raw) > HREF_MAX:
        return None, f"Is longer than {HREF_MAX} characters."
    if not (
        _shaped_like_a_page(raw, hosts) and _shaped_like_a_page(unquote(raw), hosts, decoded=True)
    ):
        return None, _NOT_A_PAGE
    return raw


def _label(raw: object, brands: tuple[str, ...]) -> str | tuple[None, str]:
    label = read_text(raw, LABEL_MAX)
    if isinstance(label, tuple):
        return label
    folded = label.casefold()
    if folded in RESERVED_LABELS:
        return None, _RESERVED
    if folded in {_folded(b) for b in brands}:
        return None, _BRAND
    return label


def _wordmark(raw: object, brands: tuple[str, ...]) -> str | tuple[None, str]:
    wordmark = read_text(raw, WORDMARK_MAX)
    if isinstance(wordmark, tuple):
        return wordmark
    folded = wordmark.casefold()
    if any(folded.startswith(_folded(b)) for b in brands if b.strip()):
        return None, _BRAND
    return wordmark


def _link(
    raw: object, where: str, hosts: frozenset[str], brands: tuple[str, ...], skip: Skip
) -> HeaderLink | None:
    """A link or call to action, or ``None`` with each failing part skipped."""
    if not isinstance(raw, dict) or "label" not in raw or "href" not in raw:
        skip(where, _INCOMPLETE)
        return None
    label = _label(raw["label"], brands)
    href = read_href(raw["href"], hosts)
    if isinstance(label, tuple):
        skip(f"{where}.label", label[1])
    if isinstance(href, tuple):
        skip(f"{where}.href", href[1])
    if isinstance(label, tuple) or isinstance(href, tuple):
        return None
    return HeaderLink(label=label, href=href)


def _links(
    raw: object, hosts: frozenset[str], brands: tuple[str, ...], skip: Skip
) -> list[HeaderLink]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        skip("header.links", 'Should be a list, like [{"label": "About", "href": "/about"}].')
        return []
    links: list[HeaderLink] = []
    for i, item in enumerate(raw):
        where = f"header.links[{i}]"
        link = _link(item, where, hosts, brands, skip)
        if link is None:
            continue
        if any(link.href == kept.href for kept in links):
            skip(f"{where}.href", _DUPLICATE)
        elif len(links) == MAX_LINKS:
            skip(where, _TOO_MANY)
        else:
            links.append(link)
    return links


def read_header(
    raw: object, hosts: frozenset[str], skip: Skip, brands: tuple[str, ...] | None = None
) -> PracticeHeader | None:
    """The header block *raw* gives, each failing value passed to *skip*;
    ``None`` when nothing in it can be used.

    *hosts* are the practice's own, the only ones a full address may name;
    *brands* the names it may not take, the deployment's by default.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        skip("header", 'Should be an object, like {"wordmark": "Your practice"}.')
        return None
    brands = brand_names() if brands is None else brands
    values: dict[str, str] = {}
    for key, value in (
        ("wordmark", _wordmark(raw["wordmark"], brands) if "wordmark" in raw else None),
        ("subtitle", read_text(raw["subtitle"], SUBTITLE_MAX) if "subtitle" in raw else None),
    ):
        if isinstance(value, tuple):
            skip(f"header.{key}", value[1])
        elif value is not None:
            values[key] = value
    links = _links(raw.get("links"), hosts, brands, skip)
    cta = _link(raw["cta"], "header.cta", hosts, brands, skip) if "cta" in raw else None
    if not (values or links or cta):
        return None
    return PracticeHeader(**values, links=links, cta=cta)


def _on_hosts(link: HeaderLink, hosts: frozenset[str]) -> bool:
    return link.href.startswith("/") or _shaped_like_a_page(link.href, hosts)


def for_hosts(header: PracticeHeader, hosts: frozenset[str]) -> PracticeHeader | None:
    """*header* without full addresses on hosts no longer among *hosts*; ``None``
    when nothing is left."""
    links = [link for link in header.links if _on_hosts(link, hosts)]
    cta = header.cta if header.cta and _on_hosts(header.cta, hosts) else None
    if not (header.wordmark or header.subtitle or links or cta):
        return None
    return header.model_copy(update={"links": links, "cta": cta})
