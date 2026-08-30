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

The model never computes a date itself: for the two date-bearing rule
types (``block_date_range``, ``block_specific_dates``) it only extracts
*tokens* -- an explicit month-day/year, or a weekday plus a "this"/"next"
qualifier -- which :mod:`app.scheduling_engine.services.date_intent`
resolves deterministically against a reference date supplied by the
caller. A date-bearing sentence the model can't express as tokens (a
named holiday, an unresolvable qualifier) is rejected into
``could_not_parse`` rather than guessed at, and so is any date-type
proposal the caller can't supply a reference date for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeGuard

from ..scheduling_engine.services.date_intent import (
    DateIntent,
    DateToken,
    UnresolvableDateIntent,
    resolve_date_intent,
)
from ..settings import get_settings
from .structured_llm_gateway import (
    StructuredLLMGateway,
    StructuredOutputTruncatedError,
    get_default_structured_llm_gateway,
)

if TYPE_CHECKING:
    from datetime import date

logger = logging.getLogger(__name__)

# The eight rule types this parser covers -- an explicit allow-list, not
# "every RuleType member", so a settings-owned type added to the enum
# later is excluded automatically rather than silently picked up.
COVERED_RULE_TYPES = frozenset(
    {
        "working_hours",
        "block_day_of_week",
        "block_time_range",
        "max_per_day",
        "buffer_before",
        "buffer_after",
        "block_date_range",
        "block_specific_dates",
    }
)

# The two rule types whose params are dates -- resolved from date_intent
# tokens rather than validated directly like the other six.
_DATE_RULE_TYPES = frozenset({"block_date_range", "block_specific_dates"})

_ENFORCEMENT_LEVELS = frozenset({"hard", "soft"})

_MAX_OUTPUT_TOKENS = 2048

_LOW_CONFIDENCE_COULD_NOT_PARSE = (
    "I was not confident enough about that one to suggest a rule. Try "
    "saying it more precisely, or use the form below."
)

_DEFAULT_COULD_NOT_PARSE = (
    "Could not map that description to a supported availability rule. Try "
    "describing working hours, a blocked day or time range, a daily "
    "appointment limit, or a buffer -- or use the form below."
)

_SYSTEM_PROMPT = (
    "You map a therapist's plain-language sentence describing their "
    "scheduling availability onto a fixed set of structured rule "
    "proposals. You never invent a rule type outside the list below.\n\n"
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
    "every appointment.\n"
    "- block_date_range: date_intent describing a start and an end -- no "
    "appointments anywhere in that span.\n"
    "- block_specific_dates: date_intent listing one or more individual "
    "dates -- no appointments on any of them.\n\n"
    "For block_date_range and block_specific_dates, never write out a "
    "resolved calendar date yourself. Instead emit a date_intent object "
    "with an items list and a range flag. Each item is exactly one of:\n"
    '  * explicit: the date exactly as the person said it, as "MM-DD" if '
    'they gave no year or "YYYY-MM-DD" if they did -- copy their digits, '
    "never compute a different date.\n"
    "  * day_of_week: the 0-6 number for a weekday word, with modifier "
    '"next" if they said "next <weekday>", "this" if they said "this '
    '<weekday>", or no modifier for a bare weekday.\n'
    'Set range to true with exactly two items (start, end) for a span ("from '
    'Friday to Monday", "March 3 through March 10"); otherwise set range to '
    'false and list one item per individual date ("the 1st and the 15th", '
    '"next Friday and next Saturday"). If a date reference can\'t be '
    "expressed this way (a named holiday, something too vague to pin down), "
    "leave proposals empty and explain why in could_not_parse instead.\n\n"
    'A sentence naming several days ("9 to 5 on weekdays") becomes one '
    'proposal per day. Default enforcement to "hard"; use "soft" only '
    'for explicit preference language ("I\'d prefer not to...").\n\n'
    "Set exclusive to true only when the sentence states this is the "
    "therapist's complete set of working hours (e.g. \"I ONLY meet on "
    'Mondays and Tuesdays"), meaning the working_hours proposals in your '
    "response together fully describe when they work. Otherwise leave it "
    "false.\n\n"
    "Give every proposal a confidence: your own calibrated probability "
    "(0.0-1.0) that this exact rule is what the therapist meant. Use low "
    "values honestly -- a low-confidence proposal is dropped rather than "
    "shown, which is the outcome you want when you are unsure.\n\n"
    "REFUSING\n\n"
    "Some sentences must not be parsed at all. Return an empty proposals "
    "list, a short could_not_parse reason a therapist would understand, "
    "and a refusal_reason naming which of these applies:\n"
    '- "ambiguous": the sentence has no concrete boundary you could write '
    'down -- a vague time of day, or a hedge with no stated cutoff ("not '
    'too early", "afternoons I guess"). Guessing the boundary would '
    "silently block time the therapist meant to keep open.\n"
    '- "out_of_scope": the sentence is about something other than WHEN '
    "slots exist. Rules about WHO may book or WHICH clients are booking "
    'and intake policy, not availability: "no new patients on Fridays" '
    'limits who books, not when the therapist works, and "I only take '
    'insurance clients on Mondays" is the same. Judge by intent, not by '
    'surface form: a day name sitting next to the word "no" is not '
    "enough to make a sentence an availability rule, and these sentences "
    "deliberately look like one.\n"
    '- "multi_intent": the sentence bundles a real availability rule with '
    "an unrelated request or an immediate action -- sending an invoice, "
    "cancelling a specific appointment, anything else to be done. Refuse "
    "the whole sentence. Parsing the availability half and dropping the "
    "rest is still a guess about what was wanted, and the dropped half "
    "leaves no trace for the therapist to notice.\n\n"
    "If nothing in the sentence maps to a covered rule type for any other "
    'reason, refuse the same way with refusal_reason "ambiguous".\n\n'
    "When genuinely unsure whether something is encodable, refuse rather "
    "than guess. A confident wrong rule silently blocks or opens a "
    "therapist's calendar, which is worse than falling through to the "
    "form."
)

# The reasons a refusal can carry. Kept as an explicit tuple so the schema
# enum, the validator and the response model can't drift apart.
REFUSAL_REASONS: tuple[str, ...] = ("ambiguous", "out_of_scope", "multi_intent")

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
                    "date_intent": {
                        "type": "object",
                        "nullable": True,
                        "properties": {
                            "items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "explicit": {"type": "string", "nullable": True},
                                        "day_of_week": {"type": "integer", "nullable": True},
                                        "modifier": {"type": "string", "nullable": True},
                                    },
                                },
                            },
                            "range": {"type": "boolean"},
                        },
                    },
                    "human_summary": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["rule_type", "enforcement", "human_summary", "confidence"],
            },
        },
        "could_not_parse": {"type": "string", "nullable": True},
        "refusal_reason": {
            "type": "string",
            "nullable": True,
            "enum": [*REFUSAL_REASONS],
        },
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
    confidence: float = 1.0
    """The model's own probability that this is what was meant. A proposal
    below the configured floor is dropped rather than shown."""


@dataclass(frozen=True)
class AvailabilityParseResult:
    """Result of parsing one natural-language availability sentence."""

    proposals: list[ProposedRule] = field(default_factory=list)
    could_not_parse: str | None = None
    exclusive: bool = False
    refusal_reason: str | None = None
    """Which kind of refusal this is, when there are no proposals.

    ``could_not_parse`` says it in the therapist's words; this says it in
    a form a caller can branch on. ``None`` on a successful parse, and on
    a refusal whose reason the model didn't name."""


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


_MODIFIERS = frozenset({"this", "next"})
_DATE_PARAM_KEYS = frozenset({"start_date", "end_date", "dates"})
_RANGE_ITEM_COUNT = 2


class _DateIntentUnresolvableError(Exception):
    """A date_intent the resolver rejected -- carries the reason to show
    the therapist verbatim, distinct from the generic could_not_parse used
    for a malformed proposal."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _parse_date_token(raw: object) -> DateToken | None:
    if not isinstance(raw, dict):
        return None
    explicit = raw.get("explicit")
    day_of_week = raw.get("day_of_week")
    modifier = raw.get("modifier")
    has_explicit = isinstance(explicit, str) and explicit.strip() != ""
    has_day = _is_valid_day(day_of_week)
    if has_explicit == has_day:  # exactly one of the two must be set
        return None
    if modifier is not None and modifier not in _MODIFIERS:
        return None
    return DateToken(
        explicit=explicit if has_explicit else None,
        day_of_week=day_of_week if has_day else None,
        modifier=modifier,
    )


def _coerce_confidence(raw: object) -> float:
    """Read the model's stated confidence, treating anything unusable as 0.

    A missing or malformed value is not "certain" — it is no answer at all,
    and the floor should catch it the same way it catches a low one.
    """
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 0.0
    return min(max(float(raw), 0.0), 1.0)


def _parse_date_intent(raw: object) -> DateIntent | None:
    if not isinstance(raw, dict):
        return None
    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return None
    tokens: list[DateToken] = []
    for raw_item in raw_items:
        token = _parse_date_token(raw_item)
        if token is None:
            return None
        tokens.append(token)
    is_range = raw.get("range", False)
    if not isinstance(is_range, bool) or (is_range and len(tokens) != _RANGE_ITEM_COUNT):
        return None
    return DateIntent(items=tokens, range=is_range)


def _resolve_date_params(
    rule_type: str, raw: dict[str, Any], reference_date: date | None
) -> dict[str, Any] | None:
    """Resolve ``block_date_range``/``block_specific_dates`` params from a
    ``date_intent`` token block.

    Rejects (returns ``None``) a proposal that carries resolved params
    directly -- the model must never compute a date itself -- and any
    date-type proposal when no reference date is available. Raises
    :class:`_DateIntentUnresolvableError` when the tokens are well-formed but
    the tie-break rules can't resolve them, so the caller can surface the
    specific reason instead of a generic rejection.
    """
    if any(key in raw for key in _DATE_PARAM_KEYS) or reference_date is None:
        return None
    intent = _parse_date_intent(raw.get("date_intent"))
    if intent is None:
        return None

    resolved = resolve_date_intent(intent, reference_date)
    if isinstance(resolved, UnresolvableDateIntent):
        raise _DateIntentUnresolvableError(resolved.reason)

    if rule_type == "block_date_range" and resolved.start_date and resolved.end_date:
        return {"start_date": resolved.start_date, "end_date": resolved.end_date}
    if rule_type == "block_specific_dates" and resolved.dates is not None:
        return {"dates": resolved.dates}
    return None


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

    def parse(self, text: str, reference_date: date | None = None) -> AvailabilityParseResult:
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
                ),
                refusal_reason="ambiguous",
            )

        result = self._coerce(completion.data, reference_date)
        logger.info(
            "Availability parse result: %d proposal(s) [%s]",
            len(result.proposals),
            ",".join(p.rule_type for p in result.proposals),
        )
        return result

    def _coerce(self, data: dict[str, Any], reference_date: date | None) -> AvailabilityParseResult:
        raw_proposals = data.get("proposals")
        if not isinstance(raw_proposals, list):
            raw_proposals = []

        proposals: list[ProposedRule] = []
        for raw in raw_proposals:
            try:
                proposal = self._coerce_one(raw, reference_date) if isinstance(raw, dict) else None
            except _DateIntentUnresolvableError as exc:
                # Unlike a malformed proposal, an unresolvable-but-well-formed
                # date_intent gets its specific reason surfaced verbatim.
                return AvailabilityParseResult(
                    could_not_parse=exc.reason, refusal_reason="ambiguous"
                )
            if proposal is None:
                # Fail closed: one schema-violating proposal rejects the
                # whole response rather than silently dropping just that
                # one -- never pass a bad payload through as a proposal.
                return AvailabilityParseResult(
                    could_not_parse=_DEFAULT_COULD_NOT_PARSE, refusal_reason="ambiguous"
                )
            proposals.append(proposal)

        could_not_parse = data.get("could_not_parse")
        if not isinstance(could_not_parse, str) or not could_not_parse.strip():
            could_not_parse = None
        refusal_reason = data.get("refusal_reason")
        if refusal_reason not in REFUSAL_REASONS:
            refusal_reason = None

        floor = self._confidence_floor()
        unsure = [p for p in proposals if p.confidence < floor]
        if unsure:
            # All or nothing: one unsure proposal refuses the sentence rather
            # than showing the confident half of it. A therapist reading a
            # partial list has no way to see what was withheld, and the rule
            # they didn't get is the one that leaves time open.
            logger.info(
                "Availability parse below confidence floor: %d of %d proposal(s)",
                len(unsure),
                len(proposals),
            )
            return AvailabilityParseResult(
                could_not_parse=could_not_parse or _LOW_CONFIDENCE_COULD_NOT_PARSE,
                refusal_reason=refusal_reason or "ambiguous",
            )

        if not proposals:
            return AvailabilityParseResult(
                could_not_parse=could_not_parse or _DEFAULT_COULD_NOT_PARSE,
                refusal_reason=refusal_reason,
            )

        return AvailabilityParseResult(
            proposals=proposals,
            could_not_parse=None,
            exclusive=bool(data.get("exclusive", False)),
        )

    def _confidence_floor(self) -> float:
        return get_settings().availability_parse_confidence_floor

    def _coerce_one(self, raw: dict[str, Any], reference_date: date | None) -> ProposedRule | None:
        rule_type = raw.get("rule_type")
        if rule_type not in COVERED_RULE_TYPES:
            return None
        enforcement = raw.get("enforcement")
        if enforcement not in _ENFORCEMENT_LEVELS:
            enforcement = "hard"
        params = (
            _resolve_date_params(rule_type, raw, reference_date)
            if rule_type in _DATE_RULE_TYPES
            else _validate_params(rule_type, raw)
        )
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
            confidence=_coerce_confidence(raw.get("confidence")),
        )
