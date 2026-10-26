# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The closed registry of preference kinds.

A kind says what a row means and what it may hold: its domain, the shape of
its structured value, whether it takes a strength, whether it carries text,
and whether a client may have more than one active row of it. A row of an
unregistered kind is refused, so the store cannot drift into free-form
key/value sprawl that nothing reads.

The core kinds are registered here. Kinds are configurable per deployment: a
deployment may register more at startup with :func:`register_kind`, and they
are stored, validated and listed exactly like these.

THE SCHEDULING VALUE IS A CONTRACT. ``scheduling.availability`` and
``scheduling.hard_limit`` are read by scheduling code as data, never by a
model. Their value is the waitlist's vocabulary, so the two read one shape:

* ``days``: a non-empty list of ``mon`` .. ``sun``;
* ``times``: a non-empty list of ``morning`` / ``afternoon`` / ``evening``;
* ``modality``: ``in_person`` / ``telehealth`` / ``either``.

A slot matches a row when its weekday is in ``days``, its part of the day is in
``times``, and its modality matches (``either`` matches any). For
``availability``, ``soft`` means "prefers these" and ``hard`` means "only
these". A ``hard_limit`` is always ``hard`` and excludes every slot it matches;
its ``modality`` narrows the limit (``in_person``: cannot come in then, video
is fine).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Pydantic resolves these field types at runtime.
from ..waitlist.schemas import DayPart, Modality, Weekday  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Callable

TEXT_MAX = 280

_WEEK: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_PARTS: tuple[str, ...] = ("morning", "afternoon", "evening")


class Domain(StrEnum):
    SCHEDULING = "scheduling"
    COMMUNICATION = "communication"
    PRACTICAL = "practical"


class Strength(StrEnum):
    HARD = "hard"
    SOFT = "soft"


class TextRule(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


class ScheduleWindow(BaseModel):
    """Days, parts of the day and a format: the scheduling contract above."""

    model_config = ConfigDict(extra="forbid")

    days: list[Weekday] = Field(min_length=1)
    times: list[DayPart] = Field(min_length=1)
    modality: Modality

    @field_validator("days")
    @classmethod
    def _week_order(cls, days: list[str]) -> list[str]:
        return [d for d in _WEEK if d in days]

    @field_validator("times")
    @classmethod
    def _day_order(cls, times: list[str]) -> list[str]:
        return [t for t in _DAY_PARTS if t in times]


@dataclass(frozen=True)
class KindSpec:
    kind: str
    domain: Domain
    label: str
    #: The structured value's shape; ``None`` for a kind that carries text only.
    value_model: type[BaseModel] | None = None
    #: Strengths the kind accepts; empty for a kind that takes none.
    strengths: frozenset[Strength] = frozenset()
    #: Used when a write names no strength.
    default_strength: Strength | None = None
    text: TextRule = TextRule.OPTIONAL
    #: At most one active row per client.
    single_active: bool = False
    #: Plain words for a value, for kinds the chart has no dedicated view of.
    summarize: Callable[[dict[str, Any]], str] | None = None


class InvalidPreferenceError(ValueError):
    """A write that does not fit its kind; the message says why, in plain words."""


@dataclass(frozen=True)
class Normalized:
    domain: Domain
    value: dict[str, Any] | None
    text: str | None
    strength: Strength | None


_REGISTRY: dict[str, KindSpec] = {}


def register_kind(spec: KindSpec) -> None:
    """Add a kind. Registering the same name twice replaces the earlier spec."""
    if spec.strengths and spec.default_strength not in spec.strengths:
        raise ValueError(f"{spec.kind}: default strength must be one it accepts")
    if spec.value_model is None and spec.text is not TextRule.REQUIRED:
        raise ValueError(f"{spec.kind}: a kind with no value must require text")
    _REGISTRY[spec.kind] = spec


def get_kind(kind: str) -> KindSpec | None:
    return _REGISTRY.get(kind)


def registered_kinds() -> list[KindSpec]:
    return list(_REGISTRY.values())


def normalize(
    kind: str,
    value: dict[str, Any] | None,
    text: str | None,
    strength: str | None,
) -> Normalized:
    """Check a write against its kind and return what is stored."""
    spec = get_kind(kind)
    if spec is None:
        raise InvalidPreferenceError(f"Unknown preference kind: {kind}")
    return Normalized(
        domain=spec.domain,
        value=_value(spec, value),
        text=_text(spec, text),
        strength=_strength(spec, strength),
    )


def _value(spec: KindSpec, value: dict[str, Any] | None) -> dict[str, Any] | None:
    if spec.value_model is None:
        if value is not None:
            raise InvalidPreferenceError("This kind takes words, not a value.")
        return None
    if value is None:
        raise InvalidPreferenceError("This kind needs a value.")
    try:
        return spec.value_model.model_validate(value).model_dump(mode="json")
    except ValidationError as exc:
        raise InvalidPreferenceError(_first_error(exc)) from exc


def _text(spec: KindSpec, text: str | None) -> str | None:
    cleaned = (text or "").strip() or None
    if spec.text is TextRule.NONE and cleaned is not None:
        raise InvalidPreferenceError("This kind takes no text.")
    if spec.text is TextRule.REQUIRED and cleaned is None:
        raise InvalidPreferenceError("Add a few words.")
    if cleaned is not None and len(cleaned) > TEXT_MAX:
        raise InvalidPreferenceError(f"Keep it to {TEXT_MAX} characters.")
    return cleaned


def _strength(spec: KindSpec, strength: str | None) -> Strength | None:
    if not spec.strengths:
        if strength is not None:
            raise InvalidPreferenceError("This kind takes no strength.")
        return None
    if strength is None:
        return spec.default_strength
    if strength not in {s.value for s in spec.strengths}:
        names = ", ".join(sorted(s.value for s in spec.strengths))
        raise InvalidPreferenceError(f"Strength must be one of: {names}.")
    return Strength(strength)


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"])
    return f"{where}: {error['msg']}" if where else str(error["msg"])


AVAILABILITY = "scheduling.availability"
HARD_LIMIT = "scheduling.hard_limit"
COMMUNICATION_NOTE = "communication.note"
PRACTICAL_NOTE = "practical.note"

CORE_KINDS: tuple[KindSpec, ...] = (
    KindSpec(
        kind=AVAILABILITY,
        domain=Domain.SCHEDULING,
        label="Times that suit",
        value_model=ScheduleWindow,
        strengths=frozenset({Strength.HARD, Strength.SOFT}),
        default_strength=Strength.SOFT,
        single_active=True,
    ),
    KindSpec(
        kind=HARD_LIMIT,
        domain=Domain.SCHEDULING,
        label="Times that don't work",
        value_model=ScheduleWindow,
        strengths=frozenset({Strength.HARD}),
        default_strength=Strength.HARD,
    ),
    KindSpec(
        kind=COMMUNICATION_NOTE,
        domain=Domain.COMMUNICATION,
        label="Communication",
        text=TextRule.REQUIRED,
        single_active=True,
    ),
    KindSpec(
        kind=PRACTICAL_NOTE,
        domain=Domain.PRACTICAL,
        label="Practical",
        text=TextRule.REQUIRED,
        single_active=True,
    ),
)

for _spec in CORE_KINDS:
    register_kind(_spec)
