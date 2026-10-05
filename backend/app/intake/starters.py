# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Consent documents a practice can start from instead of a blank page.

A starter is a document plus the questions that go with it. Adding one to a
form creates the document as the practice's own — published, so the form can
go live, and from then on edited like anything else the practice wrote — and
appends a consent item pointing at it and the starter's questions after it.
Nothing here is read again once a practice has its copy: the copy is theirs.

A starter's text is checked the same way a practice's is (see the tests): it
must parse through :mod:`app.intake.documents` and its digest is pinned, so a
change to the wording is a deliberate change to a pinned value rather than a
drift nobody noticed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .items import ItemDraft

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
    """One document a practice can start from.

    ``document_item_key`` names the consent item that points at the
    document; ``questions`` follow it on the form, in order.
    """

    key: str
    title: str
    body_markdown: str
    document_item_key: str
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

#: Every starter, in the order the editor lists them.
STARTERS: tuple[Starter, ...] = (AI_TOOLS_CONSENT,)


def starter(key: str) -> Starter | None:
    """The starter named *key*, or ``None``."""
    return next((s for s in STARTERS if s.key == key), None)


__all__ = [
    "AI_TOOLS_CONSENT",
    "AI_TOOLS_DOCUMENT_ITEM_KEY",
    "AI_TRANSCRIPTION_DECISIONS",
    "AI_TRANSCRIPTION_ITEM_KEY",
    "STARTERS",
    "Starter",
    "starter",
]
