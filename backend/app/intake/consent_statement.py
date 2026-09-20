# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The sentence somebody agrees to when they type their name.

A consent document says what is being agreed to. This says what typing a
name *is*. They are different statements and they change on different
clocks: a practice rewrites its documents whenever its paperwork changes,
and this wording changes only when the product changes what a typed name
means.

So it lives here, versioned, rather than in the document text or in the
component that draws the screen. A stored signature records
``consent_statement_version``, which is what makes "what did this person
actually agree to" answerable years later: the document's digest names the
words they read, and the version below names the sentence they agreed under.

**Nothing here claims a signature class.** No statute is named, no
jurisdiction is asserted, and the wording deliberately does not say the
signature is "legally binding" or "the equivalent of a wet signature" —
those are claims about law that depend on where a practice operates and on
facts this code cannot check. What is recorded is what happened: a person
was shown these words, typed a name, and confirmed it.

Adding a version is additive. Old rows keep pointing at the old string, so
:func:`statement_for` has to keep answering for every version ever stored —
which is why the mapping is append-only and the test that walks it exists.
"""

from __future__ import annotations

from types import MappingProxyType

#: The version a signature taken today records.
CURRENT_CONSENT_STATEMENT_VERSION = "1"

#: Every version of the wording, keyed by the string a row stores.
#:
#: **Append-only.** A stored signature names one of these keys, and the
#: whole point of recording the version is that the sentence it names can
#: still be read back. Editing an entry here would silently change what
#: every existing signature says it agreed to.
CONSENT_STATEMENTS: MappingProxyType[str, str] = MappingProxyType(
    {
        "1": (
            "By typing my name I agree that this is my electronic signature "
            "and that I have read this document."
        ),
    }
)


def consent_statement(version: str | None = None) -> str:
    """The wording for *version*, or today's when none is named.

    Raises ``KeyError`` for a version that was never shipped. That is the
    right failure: a row naming an unknown version is a record we cannot
    read back, and answering with today's wording instead would report the
    wrong sentence as the one somebody agreed to.
    """
    return CONSENT_STATEMENTS[version or CURRENT_CONSENT_STATEMENT_VERSION]


__all__ = [
    "CONSENT_STATEMENTS",
    "CURRENT_CONSENT_STATEMENT_VERSION",
    "consent_statement",
]
