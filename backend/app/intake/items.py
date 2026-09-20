# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What an intake item is, and what each kind of item has to be given.

Every item on a form has a type and a blob of configuration, and the two are
stored in separate columns: the type in a constrained ``VARCHAR``, the
configuration in JSONB. A JSONB column will hold anything, so the constraint
that makes a stored item meaningful lives here — a discriminated union, one
member per type, that says exactly what that type's configuration looks like.

Everything reads this module rather than deciding for itself. The publish
route validates against it, the settings editor's forms are built from the
same shapes, and the portal renderer walks the same union. A new item type is
a member added here plus a renderer; nothing else has a list to update.

Two properties are worth stating because they are easy to lose.

* **The type is not inside the configuration.** It rides the column, and is
  spliced in only to pick a union member. Storing it twice invites the two
  copies to disagree, and the column is the one the database constrains.
* **Validation happens at publish, not at every keystroke.** A draft is a
  work in progress and may hold a half-filled item; a published version may
  not. That is why :func:`validate_item_list` is a function the service
  calls at one moment rather than a model the API always parses through.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from ..outcome_measures.instruments import INSTRUMENT_REGISTRY
from ..outcome_measures.item_text import ITEM_TEXT
from .rules import DISPLAY_ONLY_TARGETS, ReferencedItem, RuleError, VisibleWhen, check_rule

#: Every item type, in the order the editor offers them. Mirrored by the
#: ``ck_intake_item_definitions_type`` check constraint; a test pins the two
#: together so a type added here cannot be stored until the column allows it.
ITEM_TYPES: tuple[str, ...] = (
    "section",
    "instructions",
    "demographics",
    "reason",
    "free_text",
    "single_choice",
    "multi_choice",
    "yes_no",
    "scale",
    "number",
    "date",
    "instrument",
    "emergency_contact",
    "guardian",
    "consent_document",
    "insurance_card",
    "document_request",
)

#: Items that show text and collect nothing. They are never required, are
#: excluded from completion, and nothing may branch on them.
DISPLAY_ONLY_ITEM_TYPES = DISPLAY_ONLY_TARGETS

#: Measures a patient can be asked to complete themselves. An instrument in
#: the registry with no patient-facing item text is a clinician-rated one:
#: it scores fine and cannot be put on a form.
SELF_REPORT_INSTRUMENTS = frozenset(INSTRUMENT_REGISTRY) & frozenset(ITEM_TEXT)

#: An item key is the editor's own name for a question, and rules point at it.
#: Constrained so it reads as a name in a rule rather than as an id.
ITEM_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

_MAX_OPTIONS = 40

#: Given a document key, the id of its newest published version — or
#: ``None`` if that document has never been published. A callable rather
#: than a repository so that this module keeps knowing nothing about
#: storage: the question a form asks about a document is "can somebody sign
#: it", and this is that question with nothing else attached.
type PublishedDocumentLookup = Callable[[str], str | None]


class ItemConfigError(ValueError):
    """An item that cannot be published as configured."""


def stored_config(value: object) -> dict[str, object]:
    """A stored ``config`` column read back as a mapping.

    The repositories hand rows back as ``dict[str, object]``, so the JSONB
    column arrives as ``object``. Anything that is not a mapping reads as an
    empty configuration rather than raising: the column is a record of what
    an editor sent, and a row written by an older editor is a normal thing to
    find. Publishing is where a configuration has to be right.
    """
    return dict(value) if isinstance(value, dict) else {}


class _BaseConfig(BaseModel):
    """Fields every item's configuration may carry."""

    model_config = ConfigDict(extra="forbid")

    visible_when: VisibleWhen | None = None


class _NoConfig(_BaseConfig):
    """An item whose shape is fixed by the engine, not by the practice.

    ``demographics`` asks the chart's own identity fields, ``reason`` asks
    what brings the patient in, ``emergency_contact`` and ``guardian`` ask
    their standard blocks. There is nothing to configure, which is why they
    share one member.
    """

    item_type: Literal["demographics", "reason", "emergency_contact", "guardian"]


class SectionConfig(_BaseConfig):
    """A heading that groups the items after it."""

    item_type: Literal["section"]
    title: str = Field(min_length=1, max_length=120)


class InstructionsConfig(_BaseConfig):
    """A block of text the patient reads and does not answer."""

    item_type: Literal["instructions"]
    body_markdown: str = Field(min_length=1, max_length=4000)


class FreeTextConfig(_BaseConfig):
    item_type: Literal["free_text"]
    max_len: int = Field(default=2000, ge=1, le=10000)


class ChoiceOption(BaseModel):
    """One answer a choice question offers.

    ``key`` is what gets stored and what a rule compares against, so it
    survives relabelling; ``label`` is what the patient reads.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z0-9][a-z0-9_]{0,39}$")
    label: str = Field(min_length=1, max_length=160)


class _ChoiceConfig(_BaseConfig):
    options: list[ChoiceOption] = Field(min_length=2, max_length=_MAX_OPTIONS)

    def _reject_duplicate_options(self) -> None:
        keys = [option.key for option in self.options]
        duplicate = next((k for k in keys if keys.count(k) > 1), None)
        if duplicate is not None:
            raise ValueError(f"two answers share the name {duplicate!r}")


class SingleChoiceConfig(_ChoiceConfig):
    """Pick one of several answers."""

    item_type: Literal["single_choice"]

    @model_validator(mode="after")
    def _check(self) -> SingleChoiceConfig:
        self._reject_duplicate_options()
        return self


class MultiChoiceConfig(_ChoiceConfig):
    """Pick any number of answers, optionally bounded.

    ``min`` above zero is what makes the question answerable-but-unanswered
    distinguishable from answered-with-nothing.
    """

    item_type: Literal["multi_choice"]
    min: int = Field(default=0, ge=0, le=_MAX_OPTIONS)
    max: int | None = Field(default=None, ge=1, le=_MAX_OPTIONS)

    @model_validator(mode="after")
    def _check(self) -> MultiChoiceConfig:
        self._reject_duplicate_options()
        if self.max is not None and self.max < self.min:
            raise ValueError("the most answers allowed is below the fewest required")
        if self.min > len(self.options):
            raise ValueError("more answers are required than the question offers")
        return self


class YesNoConfig(_BaseConfig):
    """A yes-or-no question, optionally with a box to say more.

    ``follow_up_label`` set means a "yes" also asks for text. Unset means the
    answer is the whole answer.
    """

    item_type: Literal["yes_no"]
    follow_up_label: str | None = Field(default=None, min_length=1, max_length=160)


class ScaleConfig(_BaseConfig):
    """A numbered scale with a word at each end."""

    item_type: Literal["scale"]
    min: int = Field(ge=0, le=100)
    max: int = Field(ge=1, le=100)
    min_label: str = Field(min_length=1, max_length=60)
    max_label: str = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def _check(self) -> ScaleConfig:
        if self.max <= self.min:
            raise ValueError("the top of the scale is not above the bottom")
        return self


class NumberConfig(_BaseConfig):
    item_type: Literal["number"]
    min: float | None = None
    max: float | None = None
    unit: str | None = Field(default=None, min_length=1, max_length=24)

    @model_validator(mode="after")
    def _check(self) -> NumberConfig:
        if self.min is not None and self.max is not None and self.max < self.min:
            raise ValueError("the largest number allowed is below the smallest")
        return self


class DateConfig(_BaseConfig):
    """A date, optionally bounded.

    ``past_only`` is the common case (a date of birth, when something
    started) and is cheaper for a practice to set than a floating bound.
    """

    item_type: Literal["date"]
    min: str | None = None
    max: str | None = None
    past_only: bool = False


class InstrumentConfig(_BaseConfig):
    """A scored measure, asked with the registry's own items and scale."""

    item_type: Literal["instrument"]
    code: str

    @model_validator(mode="after")
    def _check(self) -> InstrumentConfig:
        if self.code not in SELF_REPORT_INSTRUMENTS:
            offered = ", ".join(sorted(SELF_REPORT_INSTRUMENTS))
            raise ValueError(f"{self.code!r} is not a measure a patient can fill in ({offered})")
        return self


class ConsentDocumentConfig(_BaseConfig):
    """A document the patient reads and signs.

    Two ids, and the difference between them is the whole design.
    ``document_key`` is the document — what the practice picked in the
    editor, and what stays the same across every revision of that text.
    ``document_version_id`` is the exact revision, pinned by the publisher
    when the form is frozen, so a signature can always be read back against
    the words that were on the screen.

    A practice never sets the second one: it is absent on a draft and
    present on every published version. That is why it is optional here
    rather than required — the same model has to parse an item mid-edit and
    an item that has gone live.
    """

    item_type: Literal["consent_document"]
    document_key: str
    document_version_id: str | None = None


class InsuranceCardConfig(_BaseConfig):
    """A photo of an insurance card, front and usually back."""

    item_type: Literal["insurance_card"]
    label: str = Field(min_length=1, max_length=120)
    sides: Literal["front", "both"] = "both"


class DocumentRequestConfig(_BaseConfig):
    """Any other file the practice asks for."""

    item_type: Literal["document_request"]
    label: str = Field(min_length=1, max_length=120)


ItemConfig = Annotated[
    _NoConfig
    | SectionConfig
    | InstructionsConfig
    | FreeTextConfig
    | SingleChoiceConfig
    | MultiChoiceConfig
    | YesNoConfig
    | ScaleConfig
    | NumberConfig
    | DateConfig
    | InstrumentConfig
    | ConsentDocumentConfig
    | InsuranceCardConfig
    | DocumentRequestConfig,
    Field(discriminator="item_type"),
]

_CONFIG_ADAPTER: TypeAdapter[ItemConfig] = TypeAdapter(ItemConfig)


class ItemDraft(BaseModel):
    """One item as the editor sends it, before its configuration is checked.

    ``config`` stays a plain mapping here on purpose: a draft may hold an
    item the practice is still filling in, and rejecting that at the API
    boundary would make the editor unable to save its own work in progress.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    item_type: str
    required: bool = True
    resign_on_new_version: bool = False
    config: dict[str, object] = Field(default_factory=dict)


def validate_item_config(item_type: str, config: dict[str, object]) -> ItemConfig:
    """Parse one item's configuration, or raise :class:`ItemConfigError`.

    The type is spliced in to pick the union member; a caller gets back the
    parsed model, so the same call both checks and normalises.
    """
    if item_type not in ITEM_TYPES:
        raise ItemConfigError(f"{item_type!r} is not a kind of question this form can ask")
    try:
        return _CONFIG_ADAPTER.validate_python({**config, "item_type": item_type})
    except ValidationError as exc:
        raise ItemConfigError(_first_message(exc)) from exc


def _first_message(exc: ValidationError) -> str:
    """The first problem pydantic found, in words a therapist can act on.

    A discriminated union reports the member's tag as the first element of
    every location, and the tag is the item type the caller already knows.
    Dropping it leaves the field that is actually wrong.
    """
    error = exc.errors()[0]
    parts = [str(part) for part in error["loc"] if part != "item_type"]
    if parts and parts[0] in ITEM_TYPES:
        parts = parts[1:]
    location = ".".join(parts)
    message = error["msg"].removeprefix("Value error, ")
    return f"{location}: {message}" if location else message


def validate_item_list(
    items: list[ItemDraft],
    *,
    published_document: PublishedDocumentLookup | None = None,
) -> list[ItemConfig]:
    """Check a whole version's items the way publishing does.

    Everything that cannot be judged one item at a time happens here: keys
    are unique, a rule points backwards at a question that can answer it,
    and a consent item names a document somebody can actually sign.

    ``published_document`` answers the published version id for a document
    key, or ``None`` if that document has never been published. It is
    optional because the question is about the practice's documents rather
    than about the item, and a caller holding no document store has nothing
    to ask; left out, a consent item is checked for shape and not for what
    it points at. The route that publishes always supplies one, so a form
    cannot go live naming a document nobody could sign.

    Raises :class:`ItemConfigError` naming the item that is wrong. The
    message is what the editor shows next to that item, so it says what to do
    rather than what failed.
    """
    if not items:
        raise ItemConfigError("A form needs at least one question before it can be published.")

    seen: dict[str, ReferencedItem] = {}
    parsed: list[ItemConfig] = []

    for item in items:
        if not ITEM_KEY_PATTERN.match(item.key):
            raise ItemConfigError(
                f"{item.key!r} cannot be a question's name — use lowercase letters, "
                "numbers and underscores."
            )
        if item.key in seen:
            raise ItemConfigError(f"Two questions are both named {item.key!r}.")

        try:
            config = validate_item_config(item.item_type, item.config)
        except ItemConfigError as exc:
            raise ItemConfigError(f"{item.key}: {exc}") from exc

        if (
            isinstance(config, ConsentDocumentConfig)
            and published_document is not None
            and published_document(config.document_key) is None
        ):
            raise ItemConfigError(
                f"{item.key}: publish this document before you ask anybody to sign it."
            )

        if config.visible_when is not None:
            _check_visibility(item.key, config.visible_when, seen)

        seen[item.key] = _as_reference(item.key, item.item_type, config)
        parsed.append(config)

    return parsed


def _check_visibility(key: str, rule: VisibleWhen, earlier: dict[str, ReferencedItem]) -> None:
    """Refuse a rule that points forwards, nowhere, or at the wrong shape."""
    target = earlier.get(rule.item_key)
    if target is None:
        raise ItemConfigError(
            f"{key}: nothing earlier on this form is named {rule.item_key!r}, so this "
            "question cannot depend on it."
        )
    try:
        check_rule(rule, target)
    except RuleError as exc:
        raise ItemConfigError(f"{key}: {exc}") from exc


def _as_reference(key: str, item_type: str, config: ItemConfig) -> ReferencedItem:
    """Flatten a parsed item to what a later item's rule needs to know."""
    options: frozenset[str] = frozenset()
    if isinstance(config, SingleChoiceConfig | MultiChoiceConfig):
        options = frozenset(option.key for option in config.options)
    item_count: int | None = None
    if isinstance(config, InstrumentConfig):
        item_count = INSTRUMENT_REGISTRY[config.code].item_count
    return ReferencedItem(
        key=key,
        item_type=item_type,
        option_keys=options,
        instrument_item_count=item_count,
    )


__all__ = [
    "DISPLAY_ONLY_ITEM_TYPES",
    "ITEM_KEY_PATTERN",
    "ITEM_TYPES",
    "SELF_REPORT_INSTRUMENTS",
    "ChoiceOption",
    "ConsentDocumentConfig",
    "ItemConfig",
    "ItemConfigError",
    "ItemDraft",
    "PublishedDocumentLookup",
    "VisibleWhen",
    "validate_item_config",
    "validate_item_list",
]
