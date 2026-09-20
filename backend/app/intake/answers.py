# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What a valid answer to an intake question looks like.

:mod:`app.intake.items` says what a question is; this says what answering
one means. The two are halves of the same vocabulary and live in separate
files only because one that held both would be long enough to hide either.
Every validator below is selected by the parsed config that module
produced, so there is still exactly one place that decides what an item
type is, and a type with no validator here is refused rather than waved
through.

Three properties are worth stating, because they are what the callers rely
on.

* **An answer is a mapping, always.** Even a yes-or-no answer arrives as
  ``{"yes": true}`` rather than as a bare boolean. A JSONB column would
  hold either, and answers grow fields over time — a follow-up box, a note
  beside a choice — so every answer starts as a shape that can gain one.
* **Validation is total and pure.** No database, no patient, no stored
  state. The same value and the same config always give the same answer,
  which is what lets completion be computed on read rather than written
  down and trusted.
* **The message is for the person answering.** It says what to do about the
  question rather than which field of which model failed, and it never
  repeats the value back, because the value is the patient's own words.

**Three types are answered by a route of their own, not by sending a
value.** A consent document is answered by signing it; a card photo and a
requested file are answered by uploading them. In all three cases the value
is a *reference* — to a signature row, or to the documents that arrived —
and a value the patient composed themselves would name rows that are not
theirs or do not exist. The shapes below are real (completion reads them),
but only the route that writes the row behind a reference writes the
reference. :data:`ROUTE_WRITTEN_ITEM_TYPES` is how the save path tells them
apart — it refuses those types outright, so none of these shapes is
something a patient can put on the wire.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from ..outcome_measures.instruments import (
    INSTRUMENT_REGISTRY,
    InstrumentValidationError,
    validate_item_scores,
)
from .items import (
    DateConfig,
    DocumentRequestConfig,
    FreeTextConfig,
    InstrumentConfig,
    InsuranceCardConfig,
    MultiChoiceConfig,
    NumberConfig,
    ScaleConfig,
    SingleChoiceConfig,
    YesNoConfig,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from .items import ItemConfig

#: The longest a "what brings you in" answer, or a correction to the chart,
#: may be. Fixed rather than configurable: both questions are the engine's
#: own and both ask for a paragraph.
REASON_MAX_LEN = 4000

#: Bounds on the standard contact blocks — long enough for a real name and
#: a real phone number written however somebody writes one.
CONTACT_MAX_LEN = 200

#: Questions that display text and collect nothing.
_DISPLAY_ONLY = frozenset({"section", "instructions"})

#: Questions whose answer is written by a route of its own rather than by
#: the save path.
#:
#: Load-bearing, and the reason it is a module constant rather than a check
#: inside one function: a consent item's answer says "signature <id> exists",
#: and a patient who could send that value through the ordinary save route
#: would be asserting a signature nobody took. The save path refuses every
#: type named here, so the only writer is the route that also writes the row
#: the value points at. See ``IntakeAssignmentService.save_answer``.
SIGNED_ITEM_TYPES: frozenset[str] = frozenset({"consent_document"})

#: Questions answered by sending a file, whose value names the documents
#: that arrived.
#:
#: The same reasoning as :data:`SIGNED_ITEM_TYPES` and a sharper version of
#: it: a document id is another patient's if they happen to know it, so a
#: value the patient composed could attach somebody else's file to their own
#: form. Only the artifact route writes one, and it takes the ids off rows
#: it has just checked are this patient's.
UPLOAD_ITEM_TYPES: frozenset[str] = frozenset({"insurance_card", "document_request"})

#: Everything the ordinary save route refuses. The save path asks this one
#: question rather than two, so a type added to either set above is refused
#: without a second edit.
ROUTE_WRITTEN_ITEM_TYPES: frozenset[str] = SIGNED_ITEM_TYPES | UPLOAD_ITEM_TYPES

#: The sides of an insurance card, in the order they are asked for.
CARD_SIDES: tuple[str, ...] = ("front", "back")


class AnswerError(ValueError):
    """An answer that does not fit the question it was given to."""


# ---------------------------------------------------------------------------
# Small readers, so each validator below reads as the question it asks
# ---------------------------------------------------------------------------


def _text(value: Mapping[str, object], field: str, *, max_len: int, label: str) -> str:
    raw = value.get(field)
    if not isinstance(raw, str) or not raw.strip():
        raise AnswerError(f"{label} is still blank.")
    if len(raw) > max_len:
        raise AnswerError(f"{label} is longer than this question accepts.")
    return raw


def _flag(value: Mapping[str, object], field: str, label: str) -> bool:
    raw = value.get(field)
    if not isinstance(raw, bool):
        raise AnswerError(f"{label} has not been answered yes or no.")
    return raw


def _whole_number(value: Mapping[str, object], message: str) -> int:
    raw = value.get("value")
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise AnswerError(message)
    return raw


def _as_date(raw: object) -> date:
    if not isinstance(raw, str):
        raise AnswerError("Give this as a date.")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise AnswerError("Give this as a date.") from exc


# ---------------------------------------------------------------------------
# One validator per kind of question
# ---------------------------------------------------------------------------


def validate_demographics(value: Mapping[str, object]) -> None:
    """Confirming the name and date of birth the chart already holds.

    Saying either is wrong is an attestation, not an edit: the correction is
    recorded for the clinician to read and changes nothing about the record.
    A correction stays optional even when something is flagged, because
    somebody who knows the date is wrong and cannot remember the right one
    has still answered the question.
    """
    _flag(value, "name_confirmed", "The name on your record")
    _flag(value, "dob_confirmed", "The date of birth on your record")
    corrections = value.get("corrections")
    if corrections is None:
        return
    if not isinstance(corrections, str):
        raise AnswerError("Write any correction in your own words.")
    if len(corrections) > REASON_MAX_LEN:
        raise AnswerError("That correction is longer than this question accepts.")


def validate_reason(value: Mapping[str, object]) -> None:
    _text(value, "text", max_len=REASON_MAX_LEN, label="What brings you in")


def validate_emergency_contact(value: Mapping[str, object]) -> None:
    _text(value, "name", max_len=CONTACT_MAX_LEN, label="Their name")
    _text(value, "phone", max_len=CONTACT_MAX_LEN, label="Their phone number")
    _text(value, "relationship", max_len=CONTACT_MAX_LEN, label="How you know them")


def validate_guardian(value: Mapping[str, object]) -> None:
    _text(value, "name", max_len=CONTACT_MAX_LEN, label="Their name")
    _text(value, "relationship", max_len=CONTACT_MAX_LEN, label="How you know them")


def validate_free_text(value: Mapping[str, object], config: FreeTextConfig) -> None:
    _text(value, "text", max_len=config.max_len, label="This answer")


def validate_single_choice(value: Mapping[str, object], config: SingleChoiceConfig) -> None:
    chosen = value.get("key")
    offered = {option.key for option in config.options}
    if not isinstance(chosen, str) or chosen not in offered:
        raise AnswerError("Pick one of the answers offered.")


def validate_multi_choice(value: Mapping[str, object], config: MultiChoiceConfig) -> None:
    chosen = value.get("keys")
    offered = {option.key for option in config.options}
    if not isinstance(chosen, list) or any(key not in offered for key in chosen):
        raise AnswerError("Pick from the answers offered.")
    if len(set(chosen)) != len(chosen):
        raise AnswerError("The same answer is picked twice.")
    if len(chosen) < config.min:
        raise AnswerError(f"Pick at least {config.min}.")
    if config.max is not None and len(chosen) > config.max:
        raise AnswerError(f"Pick no more than {config.max}.")


def validate_yes_no(value: Mapping[str, object], config: YesNoConfig) -> None:
    """Yes or no, and the box a yes may open.

    The follow-up is asked for only on a yes, and only when the question
    carries one. A follow-up left behind on a no is ignored rather than
    refused: somebody who changed their answer after typing should not have
    to clear the box to get past the question.
    """
    if _flag(value, "yes", "This question") and config.follow_up_label is not None:
        _text(value, "follow_up", max_len=REASON_MAX_LEN, label=config.follow_up_label)


def validate_scale(value: Mapping[str, object], config: ScaleConfig) -> None:
    point = _whole_number(value, "Pick a point on the scale.")
    if not config.min <= point <= config.max:
        raise AnswerError(f"Pick a number between {config.min} and {config.max}.")


def validate_number(value: Mapping[str, object], config: NumberConfig) -> None:
    raw = value.get("value")
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise AnswerError("This question is answered with a number.")
    if config.min is not None and raw < config.min:
        raise AnswerError(f"This answer is {config.min} or more.")
    if config.max is not None and raw > config.max:
        raise AnswerError(f"This answer is {config.max} or less.")


def validate_date(value: Mapping[str, object], config: DateConfig, *, today: date) -> None:
    """A date, within whatever bounds the question carries.

    ``today`` is passed in rather than read from the clock so this stays a
    pure function, and so a form left open overnight is judged against one
    consistent day rather than against two.
    """
    given = _as_date(value.get("value"))
    if config.past_only and given > today:
        raise AnswerError("Give a date that has already happened.")
    if config.min is not None and given < _as_date(config.min):
        raise AnswerError(f"Give a date on or after {config.min}.")
    if config.max is not None and given > _as_date(config.max):
        raise AnswerError(f"Give a date on or before {config.max}.")


def validate_instrument(value: Mapping[str, object], config: InstrumentConfig) -> None:
    """A scored measure, checked by the registry that will score it.

    Every item has to be answered rather than just the ones somebody felt
    like answering: a partial measure has no total, and the total is what a
    clinician reads off it. Delegating the keys and the range to the
    registry's own validator is what stops this drifting from the scorer.
    """
    scores = value.get("item_scores")
    if not isinstance(scores, dict):
        raise AnswerError("This measure has not been answered yet.")
    definition = INSTRUMENT_REGISTRY[config.code]
    try:
        validate_item_scores(definition, scores)
    except InstrumentValidationError as exc:
        # The registry's message names an item key and a range, never an
        # answer — safe to show, and more useful than a generic refusal.
        raise AnswerError(str(exc)) from exc
    missing = sorted(definition.valid_keys - set(scores), key=int)
    if missing:
        raise AnswerError(f"Question {missing[0]} has not been answered yet.")


def validate_consent_document(value: Mapping[str, object]) -> None:
    """A consent item, answered by pointing at the signature that was taken.

    ``{"signed": true, "signature_id": "…"}`` and nothing else. The value is
    a reference rather than content: what was agreed to lives on the
    signature row, which carries the document version, the digest of the
    words, who signed and under which wording.

    ``signed`` is false while a document still needs a signature somebody
    has not given — a document a practice asks a guardian to sign as well as
    the patient sits there with one signature and is not finished. So this
    refuses it, and completion reports the item as outstanding, which is the
    truthful answer rather than the convenient one.
    """
    if value.get("signed") is not True:
        raise AnswerError("This document still needs to be signed.")
    signature_id = value.get("signature_id")
    if not isinstance(signature_id, str) or not signature_id.strip():
        raise AnswerError("This document still needs to be signed.")


def _attached(value: Mapping[str, object], message: str) -> list[Mapping[str, object]]:
    """The documents an upload answer names, or raise with *message*.

    One shape for both file-backed questions: a list of entries, each
    naming a document and — on a card — which side of it. A list of bare
    ids would have made the card's sides a second parallel structure to
    keep in step with it, and the first thing to go out of step would be
    which photograph is the back.
    """
    attached = value.get("documents")
    if not isinstance(attached, list) or not attached:
        raise AnswerError(message)
    entries: list[Mapping[str, object]] = []
    for entry in attached:
        if not isinstance(entry, dict):
            raise AnswerError(message)
        document_id = entry.get("document_id")
        if not isinstance(document_id, str) or not document_id.strip():
            raise AnswerError(message)
        entries.append(entry)
    return entries


def validate_insurance_card(value: Mapping[str, object], config: InsuranceCardConfig) -> None:
    """A photograph of each side the question asked for.

    ``sides: "both"`` wants a front and a back; ``"front"`` wants a front.
    Either way each side is present once: two photographs of the front and
    none of the back is a card nobody can read the plan off, and reporting
    that as answered would put it in front of a clinician as complete.

    What is deliberately not checked here is whether the documents exist or
    whose they are. This function is pure — see the module docstring — and
    the artifact route is what puts an id in this value, having just
    checked the row is the calling patient's.
    """
    wanted = CARD_SIDES if config.sides == "both" else CARD_SIDES[:1]
    entries = _attached(value, "Add a photo of your card.")
    seen = [entry.get("side") for entry in entries]
    for side in wanted:
        if seen.count(side) != 1:
            label = "front" if side == "front" else "back"
            raise AnswerError(f"Add one photo of the {label} of your card.")
    if any(side not in wanted for side in seen):
        raise AnswerError("Remove the photos this question did not ask for.")


def validate_document_request(
    value: Mapping[str, object],
    config: DocumentRequestConfig,  # noqa: ARG001 — the accepted types are the upload's to enforce
) -> None:
    """At least one file, whatever the practice asked for.

    ``accept`` is not re-checked here. What kind of file arrived is a fact
    about the stored object, and it was settled when the upload was
    finished — by the signed URL, by the recheck of the blob, and by the
    byte-level sniff. A second opinion from a value the patient never
    composed would only ever be a copy of that one.
    """
    _attached(value, "Add the file this question asks for.")


def validate_upload(value: Mapping[str, object], config: ItemConfig) -> None:
    """Either kind of file-backed question.

    One entry point because the two share a value shape and differ only in
    what counts as enough of it. The dispatch is here rather than as a
    second branch in :func:`validate_answer` so the chain there stays one
    line per kind of question rather than one per config class.
    """
    if isinstance(config, InsuranceCardConfig):
        validate_insurance_card(value, config)
    else:
        validate_document_request(value, config)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def validate_answer(config: ItemConfig, value: object, *, today: date | None = None) -> None:
    """Check one answer against the question it answers.

    Raises :class:`AnswerError` carrying a message the person answering can
    act on. ``today`` is only read by a date question and defaults to the
    caller's own day.
    """
    if config.item_type in _DISPLAY_ONLY:
        raise AnswerError("This part of the form is not a question.")
    if not isinstance(value, dict):
        raise AnswerError("This answer is not in a shape the form can store.")

    if isinstance(config, FreeTextConfig):
        validate_free_text(value, config)
    elif isinstance(config, SingleChoiceConfig):
        validate_single_choice(value, config)
    elif isinstance(config, MultiChoiceConfig):
        validate_multi_choice(value, config)
    elif isinstance(config, YesNoConfig):
        validate_yes_no(value, config)
    elif isinstance(config, ScaleConfig):
        validate_scale(value, config)
    elif isinstance(config, NumberConfig):
        validate_number(value, config)
    elif isinstance(config, DateConfig):
        validate_date(value, config, today=today or date.today())
    elif isinstance(config, InstrumentConfig):
        validate_instrument(value, config)
    elif isinstance(config, InsuranceCardConfig | DocumentRequestConfig):
        validate_upload(value, config)
    else:
        # Everything whose shape the practice does not configure. These are
        # told apart by their item type rather than by their config class:
        # four of them share one config member, and adding a branch per type
        # to the chain above would say nothing the mapping does not.
        _FIXED_SHAPE[config.item_type](value)


#: Questions whose answer has a shape a practice cannot configure, keyed by
#: item type.
#:
#: Four are the engine's own questions, which all parse to the same config
#: member. The fifth is a consent document, whose answer is a reference to
#: the signature that settled it — a shape nothing in the item's
#: configuration varies either.
_FIXED_SHAPE = {
    "demographics": validate_demographics,
    "reason": validate_reason,
    "emergency_contact": validate_emergency_contact,
    "guardian": validate_guardian,
    "consent_document": validate_consent_document,
}


def is_answered(config: ItemConfig, value: object, *, today: date | None = None) -> bool:
    """True when *value* would be accepted as an answer to this question."""
    try:
        validate_answer(config, value, today=today)
    except AnswerError:
        return False
    return True


__all__ = [
    "CARD_SIDES",
    "CONTACT_MAX_LEN",
    "REASON_MAX_LEN",
    "ROUTE_WRITTEN_ITEM_TYPES",
    "SIGNED_ITEM_TYPES",
    "UPLOAD_ITEM_TYPES",
    "AnswerError",
    "is_answered",
    "validate_answer",
    "validate_consent_document",
    "validate_date",
    "validate_demographics",
    "validate_document_request",
    "validate_emergency_contact",
    "validate_free_text",
    "validate_guardian",
    "validate_instrument",
    "validate_insurance_card",
    "validate_multi_choice",
    "validate_number",
    "validate_reason",
    "validate_scale",
    "validate_single_choice",
    "validate_upload",
    "validate_yes_no",
]
