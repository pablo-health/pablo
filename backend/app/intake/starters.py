# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Documents and questions a practice can start from instead of a blank page.

A starter is a document plus the questions that go with it, or a set of
questions alone. Adding one to a form creates the document as the practice's
own — published, so the form can go live, and from then on edited like
anything else the practice wrote — and appends a consent item pointing at it
and the starter's questions after it. Nothing here is read again once a
practice has its copy: the copy is theirs.

The built-in starters are :data:`STARTERS`. A deployment can add its own
starter documents and questions with :func:`register_intake_starter`;
:func:`intake_starters` is what the editor lists, built-ins first and then
the registered ones in the order they were registered.

Every starter is checked by :func:`check_starter`: its text must parse
through :mod:`app.intake.documents` and its questions must publish behind
the document. The built-ins also have their digest pinned in the tests, so a
change to the wording is a deliberate change to a pinned value rather than a
drift nobody noticed; a deployment registering its own can pin theirs the
same way with :func:`app.intake.documents.content_digest`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .documents import canonical_text
from .items import ITEM_KEY_PATTERN, ItemDraft, validate_item_list

#: The question on the AI-tools starter whose answer goes on the client's
#: chart as their AI-notes answer, and what each option means there. Found on
#: a submitted form by this key, so a practice that renames the question
#: keeps the form and loses the chart entry — the same as writing its own.
AI_TRANSCRIPTION_ITEM_KEY = "ai_transcription"
AI_TRANSCRIPTION_DECISIONS: dict[str, str] = {
    "consent": "consented",
    "decline": "declined",
}

#: The consent item the AI-tools starter adds. The day its signature was
#: given is the day the transcription answer takes effect.
AI_TOOLS_DOCUMENT_ITEM_KEY = "ai_tools_consent"


@dataclass(frozen=True)
class Starter:
    """One document, or one set of questions, a practice can start from.

    ``document_item_key`` names the consent item that points at the
    document; ``questions`` follow it on the form, in order. A starter with
    no ``body_markdown`` has no document and no consent item, only its
    questions.
    """

    key: str
    title: str
    body_markdown: str | None = None
    document_item_key: str | None = None
    questions: tuple[ItemDraft, ...] = ()


# Why each sentence is there, because the document itself does not say:
#
# * Addressed to the client in the second person, with no pronoun for the
#   provider, per docs/reference/copy-style.md.
# * "Draft notes" is the one AI use the client's sessions feed. The office
#   tasks are "may", because which of them a practice uses varies; reminders
#   are not on the list because nothing AI-driven sends them.
# * The provider reviews every note and answers for it: the clinician signs
#   the note, and the client should hear that a person stands behind it.
# * "Does not make decisions about your care" is the one line about what AI
#   does not do. There is no line saying AI never talks to the client: a
#   practice can offer the client an assistant between sessions, so that
#   sentence would not be true everywhere this runs.
# * Transcription gets its own section because it is the question being
#   asked. The retention period is the practice's own setting, filled in each
#   time the document is shown (see ``fill_practice_values``), never a
#   number written here.
# * "Protected the same way as the rest of your record" rather than a list of
#   safeguards: it is true, and a list would introduce machinery.
# * Changing the answer: the client tells the provider, who records it on
#   the chart. Saying it does not affect care is the reassurance a client
#   needs to answer honestly.
_AI_TOOLS_BODY = """\
# Consent for the use of AI tools

Your provider uses software with artificial intelligence (AI) features. This \
explains how they are used and asks one question about your sessions.

## How AI is used

- It helps your provider draft notes from your sessions.
- It may also help with office tasks such as scheduling and billing paperwork.

Your provider reviews every note and is responsible for what it says. AI does \
not make decisions about your care.

## Session transcription

If you agree, the audio of your sessions is transcribed to help your provider \
write the note. Your practice keeps session audio for {{audio_retention_days}} \
days, then deletes it.

## Your information

Your health information is protected the same way as the rest of your record.

## Changing your answer

You can change your answer at any time by telling your provider. Your answer \
will not affect your care.
"""

AI_TOOLS_CONSENT = Starter(
    key="ai_tools_consent",
    title="Consent for the use of AI tools",
    body_markdown=_AI_TOOLS_BODY,
    document_item_key=AI_TOOLS_DOCUMENT_ITEM_KEY,
    questions=(
        ItemDraft(
            key=AI_TRANSCRIPTION_ITEM_KEY,
            item_type="single_choice",
            required=True,
            label="Session transcription",
            config={
                "options": [
                    {"key": "consent", "label": "I consent"},
                    {"key": "decline", "label": "I do not consent"},
                ]
            },
        ),
    ),
)

#: The built-in starters, in the order the editor lists them.
STARTERS: tuple[Starter, ...] = (AI_TOOLS_CONSENT,)

_registered: dict[str, Starter] = {}


def check_starter(chosen: Starter) -> None:
    """Raise :class:`ValueError` unless *chosen* can be added to a form.

    The document must parse into words a client can read, and its consent
    item and questions must pass the checks a form passes when it is
    published, given that the practice's copy of the document is published
    — which adopting the starter makes so. A use-restricted measure among
    the questions is not refused here: whether the practice holds the
    permission is asked when it publishes the form.
    """
    if not ITEM_KEY_PATTERN.match(chosen.key):
        raise ValueError(
            f"{chosen.key!r} cannot be a starter's key — use lowercase letters, "
            "numbers and underscores."
        )
    if not chosen.title.strip():
        raise ValueError(f"{chosen.key}: a starter needs a title.")
    items = list(chosen.questions)
    if chosen.body_markdown is None:
        if chosen.document_item_key is not None:
            raise ValueError(f"{chosen.key}: a consent item needs a document to point at.")
        if not items:
            raise ValueError(f"{chosen.key}: a starter needs a document or a question.")
    else:
        if chosen.document_item_key is None:
            raise ValueError(f"{chosen.key}: a document needs a consent item to point at it.")
        if not canonical_text(chosen.body_markdown):
            raise ValueError(f"{chosen.key}: the document has no words in it.")
        consent_item = ItemDraft(
            key=chosen.document_item_key,
            item_type="consent_document",
            config={"document_key": "the-practices-copy"},
        )
        items.insert(0, consent_item)
    validate_item_list(items, published_document=lambda _key: "published")


def register_intake_starter(chosen: Starter) -> None:
    """Add *chosen* to the starters the editor lists, after those before it.

    Raises :class:`ValueError` if it fails :func:`check_starter`, or if its
    key or title is already taken. Titles are unique because adopting a
    starter reuses the practice's published document of the same title, so
    two starters sharing one would share a document.
    """
    check_starter(chosen)
    for existing in intake_starters():
        if existing.key == chosen.key:
            raise ValueError(f"There is already a starter named {chosen.key!r}.")
        if existing.title == chosen.title:
            raise ValueError(f"There is already a starter titled {chosen.title!r}.")
    _registered[chosen.key] = chosen


def intake_starters() -> tuple[Starter, ...]:
    """Every starter, built-ins first, in the order the editor lists them."""
    return (*STARTERS, *_registered.values())


def starter(key: str) -> Starter | None:
    """The starter named *key*, or ``None``."""
    return next((s for s in intake_starters() if s.key == key), None)


def clear_registered_intake_starters() -> None:
    """Forget every registered starter. For tests."""
    _registered.clear()


__all__ = [
    "AI_TOOLS_CONSENT",
    "AI_TOOLS_DOCUMENT_ITEM_KEY",
    "AI_TRANSCRIPTION_DECISIONS",
    "AI_TRANSCRIPTION_ITEM_KEY",
    "STARTERS",
    "Starter",
    "check_starter",
    "clear_registered_intake_starters",
    "intake_starters",
    "register_intake_starter",
    "starter",
]
