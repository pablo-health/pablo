# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Turning a consent document's markdown into what a patient sees, and into
the digest that records what they saw.

A practice writes its consent documents in markdown. Two things are derived
from that text, and they have very different jobs.

* :func:`render_html` produces the HTML the patient reads. It is built by
  **escaping the source first and emitting a fixed set of tags second**, so
  no character the practice typed can ever reach the browser as markup. The
  allowlist is not a filter applied after the fact — there is nothing to
  filter, because raw HTML is never parsed in the first place. A practice
  that pastes a ``<script>`` tag into a consent document sees the words
  ``<script>`` on the page, which is what somebody proofreading the
  document would expect to see.
* :func:`canonical_text` produces the plain text the digest is taken over —
  the words, in order, with whitespace normalised and markdown's own
  punctuation removed. That string is the evidence. A signature records the
  digest of the text that was consented to, so the text has to reduce to
  the same string on every machine, in every version, forever.

**Why the markdown is parsed here rather than by a library.** The digest is
the whole evidentiary claim: it is what a signature points at years later
when somebody asks what a patient actually agreed to. A dependency's
tokenizer is free to change how it handles a trailing space or a hard
break in a minor release, and if it did, every stored digest would stop
matching its own document — silently, and with nothing in the record
looking wrong. The canonicalisation below is small enough to read in one
sitting and is pinned by tests, which is the property that matters more
than breadth of syntax. The render is deliberately the same subset, so the
words the digest covers and the words on the screen are the same words.

The subset is what a consent document is made of: headings, paragraphs,
bulleted and numbered lists, block quotes, horizontal rules, links, bold
and italic. Anything else is read as the literal text it is.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from html import escape

#: A heading: one to six hashes, a space, then the words.
_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")

#: A bullet: ``-``, ``*`` or ``+`` and a space.
_BULLET = re.compile(r"^\s{0,3}[-*+]\s+(.*)$")

#: A numbered item: digits, a dot or a bracket, and a space.
_NUMBERED = re.compile(r"^\s{0,3}\d{1,9}[.)]\s+(.*)$")

#: A block quote: ``>`` and optionally a space.
_QUOTE = re.compile(r"^\s{0,3}>\s?(.*)$")

#: A horizontal rule: three or more of ``-``, ``*`` or ``_``, spaces allowed.
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")

#: ``**bold**`` or ``__bold__``. Non-greedy so two runs on one line stay two.
_STRONG = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.DOTALL)

#: ``*italic*`` or ``_italic_``, run after bold so ``**`` is already gone.
_EMPHASIS = re.compile(r"\*(.+?)\*|_(.+?)_", re.DOTALL)

#: ``[label](target)``. The target stops at whitespace or the bracket.
_LINK = re.compile(r"\[([^\]]*)\]\(([^)\s]*)\)")

#: Inline ``code``.
_CODE = re.compile(r"`([^`]+)`")

#: Schemes a link may use. Everything else — ``javascript:`` above all, but
#: also ``data:`` and anything a browser might learn to handle later — is
#: rendered as text rather than as a destination. An allowlist rather than a
#: deny-list, so a scheme nobody thought of is refused by default.
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:")


class _Kind:
    """Block kinds, named so the two renderers read the same way."""

    PARAGRAPH = "paragraph"
    HEADING = "heading"
    BULLETS = "bullets"
    NUMBERS = "numbers"
    QUOTE = "quote"
    RULE = "rule"


@dataclass(frozen=True)
class _Block:
    """One block of the document: what it is, and the lines in it.

    ``level`` is the heading depth and is ignored by every other kind.
    """

    kind: str
    lines: tuple[str, ...]
    level: int = 0


def _blocks(markdown: str) -> list[_Block]:
    """Split the source into blocks, the one place the syntax is decided.

    Both outputs walk this list, which is what keeps the rendered page and
    the digested text describing the same document. A blank line ends
    whatever was open; nothing nests.
    """
    blocks: list[_Block] = []
    pending: list[str] = []
    kind = _Kind.PARAGRAPH

    def flush() -> None:
        nonlocal pending, kind
        if pending:
            blocks.append(_Block(kind, tuple(pending)))
        pending = []
        kind = _Kind.PARAGRAPH

    for raw in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()

        if not line.strip():
            flush()
            continue

        if _RULE.match(line):
            flush()
            blocks.append(_Block(_Kind.RULE, ()))
            continue

        heading = _HEADING.match(line)
        if heading:
            flush()
            blocks.append(_Block(_Kind.HEADING, (heading.group(2).strip(),), len(heading.group(1))))
            continue

        for pattern, list_kind in ((_BULLET, _Kind.BULLETS), (_NUMBERED, _Kind.NUMBERS)):
            item = pattern.match(line)
            if item:
                if kind != list_kind:
                    flush()
                    kind = list_kind
                pending.append(item.group(1).strip())
                break
        else:
            quote = _QUOTE.match(line)
            if quote:
                if kind != _Kind.QUOTE:
                    flush()
                    kind = _Kind.QUOTE
                pending.append(quote.group(1).strip())
            else:
                if kind not in (_Kind.PARAGRAPH, _Kind.QUOTE):
                    flush()
                pending.append(line.strip())

    flush()
    return blocks


def _link_target(target: str) -> str | None:
    """A link's destination if it is one a browser may follow, else ``None``.

    Relative destinations are refused along with unknown schemes: a consent
    document is read from a portal page whose URL means nothing to the
    practice writing the text, so a relative link could only ever point
    somewhere neither of them intended.
    """
    lowered = target.strip().lower()
    return target.strip() if lowered.startswith(_SAFE_SCHEMES) else None


def _inline_html(text: str) -> str:
    """One line's inline markup, escaped first and marked up second.

    The escape happens before any tag is emitted, so the only ``<`` in the
    result is one this function wrote. That ordering is the whole safety
    argument: there is no point at which attacker-controlled text and
    markup share a representation.
    """
    out = escape(text, quote=True)

    def link(match: re.Match[str]) -> str:
        label = match.group(1)
        target = _link_target(_unescape_target(match.group(2)))
        if target is None:
            # Not a destination we will follow. The reader still gets the
            # words and the address, in the order the document wrote them.
            return f"{label} ({match.group(2)})" if match.group(2) else label
        href = escape(target, quote=True)
        return f'<a href="{href}" rel="noopener noreferrer nofollow" target="_blank">{label}</a>'

    out = _CODE.sub(lambda m: f"<code>{m.group(1)}</code>", out)
    out = _LINK.sub(link, out)
    out = _STRONG.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", out)
    return _EMPHASIS.sub(lambda m: f"<em>{m.group(1) or m.group(2)}</em>", out)


def _unescape_target(target: str) -> str:
    """A link target read back from the escaped line.

    ``escape`` has already run by the time the link pattern matches, so an
    ``&`` in a query string arrives as ``&amp;``. Turning those back is what
    lets :func:`_link_target` see the scheme the practice actually typed —
    notably ``&#x6a;avascript:``, which would otherwise not look like a
    scheme at all.
    """
    return (
        target.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#x27;", "'")
    )


def _inline_text(text: str) -> str:
    """One line's words, with markdown's own punctuation taken off.

    A link becomes its label followed by its destination in brackets: the
    address is part of what somebody agreed to, so dropping it would let a
    document's links be repointed without the digest moving.
    """
    out = _CODE.sub(lambda m: m.group(1), text)
    out = _LINK.sub(
        lambda m: f"{m.group(1)} ({m.group(2)})" if m.group(2) else m.group(1),
        out,
    )
    out = _STRONG.sub(lambda m: m.group(1) or m.group(2), out)
    return _EMPHASIS.sub(lambda m: m.group(1) or m.group(2), out)


def render_html(markdown: str) -> str:
    """The document as HTML a patient can be shown.

    Every tag in the result was written by this function; everything that
    came from the source was escaped before it got here. The output carries
    no attributes other than a link's ``href``, ``rel`` and ``target``, so
    there is no ``on*`` handler to strip and no style to sanitise.
    """
    parts: list[str] = []
    for block in _blocks(markdown):
        if block.kind == _Kind.RULE:
            parts.append("<hr />")
        elif block.kind == _Kind.HEADING:
            tag = f"h{min(block.level + 1, 6)}"
            parts.append(f"<{tag}>{_inline_html(block.lines[0])}</{tag}>")
        elif block.kind in (_Kind.BULLETS, _Kind.NUMBERS):
            tag = "ul" if block.kind == _Kind.BULLETS else "ol"
            items = "".join(f"<li>{_inline_html(line)}</li>" for line in block.lines)
            parts.append(f"<{tag}>{items}</{tag}>")
        elif block.kind == _Kind.QUOTE:
            body = _inline_html(" ".join(block.lines))
            parts.append(f"<blockquote><p>{body}</p></blockquote>")
        else:
            parts.append(f"<p>{_inline_html(' '.join(block.lines))}</p>")
    return "\n".join(parts)


def canonical_text(markdown: str) -> str:
    """The document reduced to the words it says, in order.

    One block per line, runs of whitespace collapsed to one space, no
    markdown punctuation and no trailing newline. A list is one line per
    item so that adding an item changes the text; a heading is its words,
    because how large they were printed is not part of what was agreed.

    This string is what the digest is taken over, so its stability is the
    contract. Two documents whose words differ by a single character
    produce different text here; the same document formatted with two
    spaces after a full stop produces the same text.
    """
    lines: list[str] = []
    for block in _blocks(markdown):
        if block.kind == _Kind.RULE:
            continue
        if block.kind in (_Kind.BULLETS, _Kind.NUMBERS):
            lines.extend(_normalise(_inline_text(line)) for line in block.lines)
        elif block.kind == _Kind.HEADING:
            lines.append(_normalise(_inline_text(block.lines[0])))
        else:
            lines.append(_normalise(_inline_text(" ".join(block.lines))))
    return "\n".join(line for line in lines if line)


def _normalise(text: str) -> str:
    """Runs of any whitespace collapsed to one space, and trimmed."""
    return re.sub(r"\s+", " ", text).strip()


def content_digest(markdown: str) -> str:
    """The sha256 of the canonical text, lowercase hex.

    Taken over the words rather than over the source so that a practice
    reflowing a paragraph does not read as a different document, and a
    practice changing a word does.
    """
    return hashlib.sha256(canonical_text(markdown).encode("utf-8")).hexdigest()


__all__ = ["canonical_text", "content_digest", "render_html"]
