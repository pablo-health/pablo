# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The one authority on what an availability rule's ``params`` may hold.

``availability_rules.params`` is JSONB because the nine rule types share
almost no fields: typed columns would mean one wide table of mostly-NULL
rows, or nine tables with nine repositories, and adding a rule type would
become a migration instead of a code change. What JSONB does not buy is
permission to store any shape at all. Nothing downstream re-checks:
:class:`~app.scheduling_engine.services.availability.AvailabilityEngine`
reads params with bare subscripts, so a key that isn't there is a 500 on
the booking path rather than a complaint at save time.

Worse than a missing key is a misspelled one. ``params`` carrying
``minute`` instead of ``minutes`` is not a broken ``buffer_after`` rule —
it is a gap rule that enforces nothing, and it looks correct in the UI.
A writer and a reader that agree only by convention drift, and drift here
means a therapist's blocked time quietly stops blocking.

So every writer goes through the tagged union below, discriminated on
``rule_type``, with ``extra="forbid"`` on each member: an unknown key is
rejected rather than stored. Every :class:`RuleType` member has a model
(:data:`RULE_PARAM_MODELS`, pinned by a test), the frontend's own
validation is pinned against the JSON Schema this module emits, and the
natural-language parser validates its proposals through these same models
rather than keeping its own copy.
"""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from ..scheduling_engine.models.availability import EnforcementLevel, RuleType

__all__ = [
    "RULE_PARAM_MODELS",
    "AvailabilityRuleParamsError",
    "TaggedAvailabilityRule",
    "rule_params_json_schema",
    "validate_rule_params",
]

#: Every numeric param is a strict int: ``True`` is an ``int`` in Python
#: and Monday is not a boolean, and a number that arrived as a string is a
#: writer that isn't sending what it thinks it is.
DayOfWeek = Annotated[StrictInt, Field(ge=0, le=6, description="0=Monday through 6=Sunday")]

#: ``HH:MM`` on a 24-hour clock, which is what the engine's minute
#: arithmetic and the frontend's string comparisons both assume.
TimeOfDay = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]


def _is_a_real_date(value: str) -> str:
    """Reject a well-shaped date that isn't on the calendar (``2026-02-30``)."""
    try:
        date.fromisoformat(value)
    except ValueError as e:
        raise ValueError("must be a real calendar date as YYYY-MM-DD") from e
    return value


CalendarDate = Annotated[
    str,
    Field(pattern=r"^\d{4}-\d{2}-\d{2}$"),
    AfterValidator(_is_a_real_date),
]


class _RuleParams(BaseModel):
    """Base for every per-rule-type params model.

    ``extra="forbid"`` is the load-bearing part: a key no rule type reads
    must not be storable, because a stored one reads as a configured rule
    that enforces nothing.
    """

    model_config = ConfigDict(extra="forbid")


class _TimeRange(_RuleParams):
    start: TimeOfDay
    end: TimeOfDay

    @model_validator(mode="after")
    def _end_after_start(self) -> _TimeRange:
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


class WorkingHoursParams(_TimeRange):
    """When the therapist is available on one day of the week."""

    day_of_week: DayOfWeek


class BlockDayOfWeekParams(_RuleParams):
    """No appointments on this day of the week."""

    day_of_week: DayOfWeek


class BlockTimeRangeParams(_TimeRange):
    """No appointments in this time range, on every day.

    Deliberately no ``day_of_week``: the engine blocks the range on every
    date it evaluates, so a day would be a field nothing reads.
    """


class BlockDateRangeParams(_RuleParams):
    """No appointments anywhere in an inclusive span of dates."""

    start_date: CalendarDate
    end_date: CalendarDate

    @model_validator(mode="after")
    def _end_not_before_start(self) -> BlockDateRangeParams:
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class BlockSpecificDatesParams(_RuleParams):
    """No appointments on any of these individual dates."""

    dates: Annotated[list[CalendarDate], Field(min_length=1)]


class MaxPerDayParams(_RuleParams):
    """At most this many appointments in a day."""

    max: Annotated[StrictInt, Field(ge=1)]


class BufferBeforeParams(_RuleParams):
    """A gap required before every appointment."""

    minutes: Annotated[StrictInt, Field(ge=0)]


class BufferAfterParams(_RuleParams):
    """A gap required after every appointment."""

    minutes: Annotated[StrictInt, Field(ge=0)]


class SessionDefaultsParams(_RuleParams):
    """What a new appointment starts as, rather than what it may not be.

    Both fields are optional — an empty ``session_defaults`` is how the
    frontend clears a default it had set — and the engine falls back to
    its own duration and an unaligned grid when they're absent.
    """

    duration_minutes: Annotated[StrictInt, Field(ge=1)] | None = None
    alignment: Literal["hour", "half_hour"] | None = None


class _TaggedRule(BaseModel):
    """One member of the tagged union: a rule type and the params it takes.

    ``enforcement`` rides along because the create body carries it —
    ``hard`` refuses a booking, ``soft`` permits it and flags it.
    """

    model_config = ConfigDict(extra="forbid")

    enforcement: EnforcementLevel = EnforcementLevel.HARD


class WorkingHoursRule(_TaggedRule):
    rule_type: Literal[RuleType.WORKING_HOURS]
    params: WorkingHoursParams


class BlockDayOfWeekRule(_TaggedRule):
    rule_type: Literal[RuleType.BLOCK_DAY_OF_WEEK]
    params: BlockDayOfWeekParams


class BlockTimeRangeRule(_TaggedRule):
    rule_type: Literal[RuleType.BLOCK_TIME_RANGE]
    params: BlockTimeRangeParams


class BlockDateRangeRule(_TaggedRule):
    rule_type: Literal[RuleType.BLOCK_DATE_RANGE]
    params: BlockDateRangeParams


class BlockSpecificDatesRule(_TaggedRule):
    rule_type: Literal[RuleType.BLOCK_SPECIFIC_DATES]
    params: BlockSpecificDatesParams


class MaxPerDayRule(_TaggedRule):
    rule_type: Literal[RuleType.MAX_PER_DAY]
    params: MaxPerDayParams


class BufferBeforeRule(_TaggedRule):
    rule_type: Literal[RuleType.BUFFER_BEFORE]
    params: BufferBeforeParams


class BufferAfterRule(_TaggedRule):
    rule_type: Literal[RuleType.BUFFER_AFTER]
    params: BufferAfterParams


class SessionDefaultsRule(_TaggedRule):
    rule_type: Literal[RuleType.SESSION_DEFAULTS]
    params: SessionDefaultsParams


TaggedAvailabilityRule = Annotated[
    WorkingHoursRule
    | BlockDayOfWeekRule
    | BlockTimeRangeRule
    | BlockDateRangeRule
    | BlockSpecificDatesRule
    | MaxPerDayRule
    | BufferBeforeRule
    | BufferAfterRule
    | SessionDefaultsRule,
    Field(discriminator="rule_type"),
]
"""The create-rule body: a rule type, its enforcement, and its params.

Used directly as the request model so FastAPI answers a malformed rule
with a 422 naming the field, rather than storing it for the availability
path to trip over later.
"""

_RULE_ADAPTER: TypeAdapter[Any] = TypeAdapter(TaggedAvailabilityRule)

# Unwrap Annotated[A | B | ..., Field(discriminator=...)] to the members.
_UNION_MEMBERS = get_args(get_args(TaggedAvailabilityRule)[0])


def _tag_of(member: Any) -> RuleType:
    """The rule type a union member is tagged with."""
    (tag,) = get_args(member.model_fields["rule_type"].annotation)
    return RuleType(tag)


#: Every rule type's params model, keyed by rule type. Read off the union
#: rather than listed again, so the two can't disagree; a test pins the
#: keys against :class:`RuleType` so a new enum member fails there rather
#: than falling through to an unvalidated dict.
RULE_PARAM_MODELS: dict[RuleType, type[BaseModel]] = {
    _tag_of(member): member.model_fields["params"].annotation for member in _UNION_MEMBERS
}


class AvailabilityRuleParamsError(ValueError):
    """Params that don't match their rule type, with the field named."""


def validate_rule_params(rule_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """Validate raw ``params`` for ``rule_type`` and return them normalized.

    The update path's entry point: PATCH carries params without
    necessarily carrying a rule type, so it resolves the effective type
    from the stored rule and validates against the same union the create
    body is.

    Raises :class:`AvailabilityRuleParamsError` naming the offending
    field.
    """
    try:
        rule = _RULE_ADAPTER.validate_python({"rule_type": rule_type, "params": params})
    except ValidationError as e:
        raise AvailabilityRuleParamsError(_describe(e)) from e
    return dict(rule.params.model_dump(exclude_none=True))


def _describe(error: ValidationError) -> str:
    """Render a validation failure as ``field: reason``, comma-separated.

    Pydantic prefixes a discriminated union's locations with the tag; the
    therapist-facing part is the field under ``params``.
    """
    parts = []
    for detail in error.errors():
        loc = [str(part) for part in detail["loc"] if part != "params"]
        field = ".".join(loc[1:] if len(loc) > 1 else loc)
        parts.append(f"{field}: {detail['msg']}" if field else detail["msg"])
    return "; ".join(parts)


def rule_params_json_schema() -> dict[str, Any]:
    """The union as JSON Schema, for pinning the frontend's own validation.

    The frontend keeps a hand-written ``validate()`` because it has to say
    something to the therapist before the request goes out. This is what
    its test compares against, so a field renamed here fails there.
    """
    return _RULE_ADAPTER.json_schema()
