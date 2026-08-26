# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Parse a natural-language sentence into proposed availability rules.

Two-stage propose-then-confirm: this service NEVER creates a rule. It maps
a therapist's plain-language sentence ("no appointments on Fridays", "9 to
5 on weekdays") onto the existing ``rule_type``/``params`` schemas via a
structured LLM call, and every proposal it returns must still be confirmed
(and can be edited) by the caller through the existing create-rule
endpoint. Mapping a sentence onto a fixed schema is mechanical, not
generative, so this mirrors :class:`NoteImportService`'s flash-tier,
thinking-disabled model choice rather than the reasoning-heavy generation
path.

Deterministic date/day arithmetic stays out of scope entirely: the prompt
receives no today's-date or timezone context, so it cannot compute a
relative or year-ambiguous date, and date-bearing sentences are rejected
into ``could_not_parse`` rather than guessed at. Only the six date-free
rule types in :data:`COVERED_RULE_TYPES` are parsed; ``block_date_range``
and ``block_specific_dates`` stay out of v1 for that reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, TypeGuard

from ..settings import get_settings
from .structured_llm_gateway import (
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    get_default_structured_llm_gateway,
)

logger = logging.getLogger(__name__)

# The six date-free rule types this parser covers -- an explicit allow-list,
# not "every RuleType member", so a settings-owned or date-bearing type
# added to the enum later is excluded automatically rather than silently
# picked up.
COVERED_RULE_TYPES = frozenset(
    {
        "working_hours",
        "block_day_of_week",
        "block_time_range",
        "max_per_day",
        "buffer_before",
        "buffer_after",
    }
)

_ENFORCEMENT_LEVELS = frozenset({"hard", "soft"})

_MAX_OUTPUT_TOKENS = 2048

_DEFAULT_COULD_NOT_PARSE = (
    "Could not map that description to a supported availability rule. Try "
    "describing working hours, a blocked day or time range, a daily "
    "appointment limit, or a buffer -- or use the form below."
)

_SYSTEM_PROMPT = (
    "You map a therapist's plain-language sentence describing their "
    "scheduling availability onto a fixed set of structured rule "
    "proposals. You never invent a rule type outside the list below, and "
    "you never compute or resolve dates -- if the sentence names or "
    'implies a specific date or relative date ("next Friday", "the week '
    'of Thanksgiving", "Dec 24"), leave proposals empty and explain why '
    "in could_not_parse.\n\n"
    "Days of the week are numbered 0=Monday, 1=Tuesday, 2=Wednesday, "
    "3=Thursday, 4=Friday, 5=Saturday, 6=Sunday.\n\n"
    "Covered rule types and their params:\n"
    "- working_hours: day_of_week, start (HH:MM), end (HH:MM) -- the "
    "therapist is available on this day between start and end.\n"
    "- block_day_of_week: day_of_week -- no appointments on this day.\n"
    "- block_time_range: start (HH:MM), end (HH:MM) -- no appointments in "
    "this time range on any day.\n"
    "- max_per_day: max (integer, at least 1) -- at most this many "
    "appointments per day.\n"
    "- buffer_before: minutes (integer, at least 0) -- gap required before "
    "every appointment.\n"
    "- buffer_after: minutes (integer, at least 0) -- gap required after "
    "every appointment.\n\n"
    'A sentence naming several days ("9 to 5 on weekdays") becomes one '
    'proposal per day. Default enforcement to "hard"; use "soft" only '
    'for explicit preference language ("I\'d prefer not to...").\n\n'
    "Set exclusive to true only when the sentence states this is the "
    "therapist's complete set of working hours (e.g. \"I ONLY meet on "
    'Mondays and Tuesdays"), meaning the working_hours proposals in your '
    "response together fully describe when they work. Otherwise leave it "
    "false.\n\n"
    "If nothing in the sentence maps to a covered rule type, return an "
    "empty proposals list and a short could_not_parse reason a therapist "
    "would understand."
)

_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rule_type": {"type": "string"},
                    "enforcement": {"type": "string"},
                    "day_of_week": {"type": "integer", "nullable": True},
                    "start": {"type": "string", "nullable": True},
                    "end": {"type": "string", "nullable": True},
                    "max": {"type": "integer", "nullable": True},
                    "minutes": {"type": "integer", "nullable": True},
                    "human_summary": {"type": "string"},
                },
                "required": ["rule_type", "enforcement", "human_summary"],
            },
        },
        "could_not_parse": {"type": "string", "nullable": True},
        "exclusive": {"type": "boolean"},
    },
    "required": ["proposals"],
}


@dataclass(frozen=True)
class ProposedRule:
    """One validated, individually-confirmable rule proposal."""

    rule_type: str
    enforcement: str
    params: dict[str, Any]
    human_summary: str


@dataclass(frozen=True)
class AvailabilityParseResult:
    """Result of parsing one natural-language availability sentence."""

    proposals: list[ProposedRule] = field(default_factory=list)
    could_not_parse: str | None = None
    exclusive: bool = False


_TIME_STRING_LENGTH = 5
_MAX_HOUR = 23
_MAX_MINUTE = 59
_MAX_DAY_OF_WEEK = 6


def _is_valid_time(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or len(value) != _TIME_STRING_LENGTH or value[2] != ":":
        return False
    hours, minutes = value[:2], value[3:]
    if not (hours.isdigit() and minutes.isdigit()):
        return False
    return 0 <= int(hours) <= _MAX_HOUR and 0 <= int(minutes) <= _MAX_MINUTE


def _is_valid_day(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_DAY_OF_WEEK


def _is_valid_int(value: object, *, minimum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _validate_time_range_params(raw: dict[str, Any]) -> dict[str, Any] | None:
    start, end = raw.get("start"), raw.get("end")
    if not _is_valid_time(start) or not _is_valid_time(end) or end <= start:
        return None
    return {"start": start, "end": end}


def _validate_working_hours_params(raw: dict[str, Any]) -> dict[str, Any] | None:
    time_range = _validate_time_range_params(raw)
    day = raw.get("day_of_week")
    if time_range is None or not _is_valid_day(day):
        return None
    return {"day_of_week": day, **time_range}


def _validate_block_day_params(raw: dict[str, Any]) -> dict[str, Any] | None:
    day = raw.get("day_of_week")
    return {"day_of_week": day} if _is_valid_day(day) else None


def _validate_max_per_day_params(raw: dict[str, Any]) -> dict[str, Any] | None:
    max_value = raw.get("max")
    return {"max": max_value} if _is_valid_int(max_value, minimum=1) else None


def _validate_buffer_params(raw: dict[str, Any]) -> dict[str, Any] | None:
    minutes = raw.get("minutes")
    return {"minutes": minutes} if _is_valid_int(minutes, minimum=0) else None


_PARAM_VALIDATORS: dict[str, Any] = {
    "working_hours": _validate_working_hours_params,
    "block_day_of_week": _validate_block_day_params,
    "block_time_range": _validate_time_range_params,
    "max_per_day": _validate_max_per_day_params,
    "buffer_before": _validate_buffer_params,
    "buffer_after": _validate_buffer_params,
}


def _validate_params(rule_type: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and extract this rule type's params from a raw proposal.

    Mirrors the frontend's ``validate()`` (AvailabilitySettings.tsx) so a
    proposal that would fail the manual form's own validation is rejected
    here instead of being passed through -- the create API does not
    validate params itself.
    """
    validator = _PARAM_VALIDATORS.get(rule_type)
    return validator(raw) if validator else None


class AvailabilityRuleParseService:
    """Parse a natural-language availability sentence into rule proposals."""

    def __init__(
        self,
        llm_gateway: StructuredLLMGateway | None = None,
        model: str | None = None,
    ) -> None:
        self._llm_gateway = llm_gateway or get_default_structured_llm_gateway()
        self._model = model

    def _resolve_model(self) -> str:
        # Mapping a sentence onto a fixed schema is mechanical, not
        # generative -- same flash-tier default as note import.
        settings = get_settings()
        return self._model or settings.ai_model_flash or settings.ai_model

    def parse(self, text: str) -> AvailabilityParseResult:
        logger.info("Availability parse request: %d chars", len(text))
        try:
            completion = self._llm_gateway.complete_structured(
                model=self._resolve_model(),
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=text,
                response_schema=_RESPONSE_SCHEMA,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
                temperature=0.0,
                thinking_budget=0,
            )
        except StructuredOutputTruncatedError:
            logger.warning(
                "Availability parse truncated at max_output_tokens=%d", _MAX_OUTPUT_TOKENS
            )
            return AvailabilityParseResult(
                could_not_parse=(
                    "That description was too long to parse in one go -- try a "
                    "shorter sentence or the form below."
                )
            )

        result = self._coerce(completion.data)
        logger.info(
            "Availability parse result: %d proposal(s) [%s]",
            len(result.proposals),
            ",".join(p.rule_type for p in result.proposals),
        )
        return result

    def _coerce(self, data: dict[str, Any]) -> AvailabilityParseResult:
        raw_proposals = data.get("proposals")
        if not isinstance(raw_proposals, list):
            raw_proposals = []

        proposals: list[ProposedRule] = []
        for raw in raw_proposals:
            proposal = self._coerce_one(raw) if isinstance(raw, dict) else None
            if proposal is None:
                # Fail closed: one schema-violating proposal rejects the
                # whole response rather than silently dropping just that
                # one -- never pass a bad payload through as a proposal.
                return AvailabilityParseResult(could_not_parse=_DEFAULT_COULD_NOT_PARSE)
            proposals.append(proposal)

        could_not_parse = data.get("could_not_parse")
        if not isinstance(could_not_parse, str) or not could_not_parse.strip():
            could_not_parse = None

        if not proposals:
            return AvailabilityParseResult(
                could_not_parse=could_not_parse or _DEFAULT_COULD_NOT_PARSE
            )

        return AvailabilityParseResult(
            proposals=proposals,
            could_not_parse=None,
            exclusive=bool(data.get("exclusive", False)),
        )

    def _coerce_one(self, raw: dict[str, Any]) -> ProposedRule | None:
        rule_type = raw.get("rule_type")
        if rule_type not in COVERED_RULE_TYPES:
            return None
        enforcement = raw.get("enforcement")
        if enforcement not in _ENFORCEMENT_LEVELS:
            enforcement = "hard"
        params = _validate_params(rule_type, raw)
        if params is None:
            return None
        human_summary = raw.get("human_summary")
        if not isinstance(human_summary, str):
            human_summary = ""
        return ProposedRule(
            rule_type=rule_type,
            enforcement=enforcement,
            params=params,
            human_summary=human_summary,
        )
