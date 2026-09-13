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

An appointment type is handled the same way. A rule may be scoped to one
of the practice's types ("only two intakes a week"), but the model never
names a type on its own: the caller passes the practice's own
:class:`~app.scheduling_engine.models.appointment_type.AppointmentType`
rows, the model may only echo a name from that list, and the service
resolves the echo back to an id. A name that isn't on the list is a
question for the therapist, never a rule that quietly applies to every
kind of appointment.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from ..models.availability_rule_params import RULE_PARAM_MODELS
from ..scheduling_engine.models.availability import RuleType
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
    from collections.abc import Sequence
    from datetime import date

    from ..scheduling_engine.models.appointment_type import AppointmentType

logger = logging.getLogger(__name__)

# The nine rule types this parser covers -- an explicit allow-list, not
# "every RuleType member", so a settings-owned type added to the enum
# later is excluded automatically rather than silently picked up.
COVERED_RULE_TYPES = frozenset(
    {
        "working_hours",
        "block_day_of_week",
        "block_time_range",
        "max_per_day",
        "max_per_week",
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

_UNKNOWN_TYPE_COULD_NOT_PARSE = (
    'This practice has no appointment type called "{name}". Add the type '
    "first, or say the rule without naming one."
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
    "- max_per_week: max (integer, at least 1) -- at most this many "
    "appointments per week.\n"
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
REFUSAL_REASONS: tuple[str, ...] = (
    "ambiguous",
    "out_of_scope",
    "multi_intent",
    "unknown_appointment_type",
)

_NO_APPOINTMENT_TYPES_PROMPT = (
    "APPOINTMENT TYPES\n\n"
    "This practice has no appointment types configured, so every rule "
    "applies to every kind of appointment: leave appointment_type null on "
    "every proposal. If the sentence names a kind of appointment, leave "
    'proposals empty and refuse with refusal_reason "unknown_appointment_'
    'type".\n'
)

_APPOINTMENT_TYPES_PROMPT = (
    "APPOINTMENT TYPES\n\n"
    "This practice has these appointment types, and only these:\n"
    "{names}\n"
    "A sentence naming one of them scopes the rule to it: set "
    "appointment_type to that name copied exactly as spelled above. A "
    "sentence naming no kind of appointment leaves appointment_type null, "
    "which applies the rule to every kind -- the ordinary case.\n\n"
    "Never write an appointment_type that is not on the list. If the "
    "sentence names a kind of appointment this practice does not have, "
    'leave proposals empty and refuse with refusal_reason "unknown_'
    'appointment_type", naming in could_not_parse the kind you could not '
    "find.\n\n"
    "Set type_exclusive to true only on a working_hours proposal that is "
    "scoped to a type AND whose sentence hands that window to that type "
    'alone ("Tuesday afternoons are for intakes only"): no other kind of '
    "appointment may be offered those minutes. Scoping a window to a type "
    'without saying that ("I see intakes on Tuesday afternoons") is a '
    "narrowing, not a claim -- leave type_exclusive false.\n\n"
    "Those two readings store different rules, so a sentence supporting "
    'both is not yours to settle. "I only do intakes on Tuesdays" reads '
    'either as a narrowing ("intakes happen on Tuesdays and nowhere else") '
    'or as a claim ("Tuesdays are for intakes and nothing else"), and "two '
    'intakes a week on Tuesdays" reads either as one weekly cap or as a '
    "weekly cap plus Tuesday hours. When both readings are live, propose "
    "the reading you think likeliest, give every proposal a confidence of "
    "0.3 or below, and write could_not_parse as a short question naming "
    "the two readings, so the therapist is the one who decides.\n"
)


def _appointment_types_prompt(appointment_types: Sequence[AppointmentType]) -> str:
    """The practice's own type names, as the only names the model may use."""
    if not appointment_types:
        return _NO_APPOINTMENT_TYPES_PROMPT
    names = "\n".join(f'- "{t.name}"' for t in appointment_types)
    return _APPOINTMENT_TYPES_PROMPT.format(names=names)


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
                    "appointment_type": {"type": "string", "nullable": True},
                    "type_exclusive": {"type": "boolean", "nullable": True},
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
    appointment_type_id: str | None = None
    """Which of the practice's appointment types this rule governs, or None
    for all of them. Only ever an id the caller supplied."""
    allow_other_types: bool = True
    """False claims this rule's window for its type alone. Only meaningful
    on a type-scoped working_hours rule, exactly as the engine reads it."""


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


_MAX_DAY_OF_WEEK = 6


def _is_valid_day(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= _MAX_DAY_OF_WEEK


def _validate_params(rule_type: str, raw: dict[str, Any]) -> dict[str, Any] | None:
    """Validate and extract this rule type's params from a raw proposal.

    Validated through the shared tagged union
    (:mod:`app.models.availability_rule_params`) — the same authority the
    create API validates against — so a proposal that would be a 422 there
    is dropped here instead of being shown to the therapist as a rule they
    can confirm.

    The model returns every param flat on the proposal, so the params
    model's own field names select which of them belong to this rule type.
    Anything else the model invented is not carried forward, and a key it
    invented *in place of* a real one fails the model as a missing field.
    """
    model = RULE_PARAM_MODELS.get(RuleType(rule_type)) if rule_type in COVERED_RULE_TYPES else None
    if model is None:
        return None
    candidate = {name: raw[name] for name in model.model_fields if name in raw}
    try:
        return dict(model(**candidate).model_dump(exclude_none=True))
    except ValidationError:
        return None


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


class _UnknownAppointmentTypeError(Exception):
    """A type name the practice does not have -- carries the name so the
    therapist is asked about the words they actually used."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.name = name


def _normalize_type_name(name: str) -> str:
    """A type name reduced to what a sentence and a settings row can share.

    Case and a trailing plural are the two differences between "Intake" on
    the settings page and "intakes" in a sentence; nothing else is guessed
    at, so a name the practice does not have stays unresolvable.
    """
    return " ".join(name.split()).casefold().removesuffix("s")


def _index_appointment_types(appointment_types: Sequence[AppointmentType]) -> dict[str, str]:
    """The practice's types keyed by normalized name.

    Two types whose names differ only by case or a trailing "s" cannot be
    told apart from a sentence, so neither is bound: the name resolves as
    unknown and the therapist is asked, rather than one of the two being
    picked for them.
    """
    index: dict[str, str] = {}
    ambiguous: set[str] = set()
    for appointment_type in appointment_types:
        key = _normalize_type_name(appointment_type.name)
        if index.get(key, appointment_type.id) != appointment_type.id:
            ambiguous.add(key)
        index[key] = appointment_type.id
    for key in ambiguous:
        del index[key]
    return index


def _resolve_appointment_type(raw: object, index: dict[str, str]) -> str | None:
    """The id of the type the model named, or None when it named none.

    Raises :class:`_UnknownAppointmentTypeError` for a name the practice
    does not have. Falling back to None there would turn "no intakes on
    Fridays" into a rule that blocks every kind of appointment, which is
    the one outcome nobody asked for.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    resolved = index.get(_normalize_type_name(raw))
    if resolved is None:
        raise _UnknownAppointmentTypeError(raw.strip())
    return resolved


def _claims_its_window(
    rule_type: str, appointment_type_id: str | None, raw: dict[str, Any]
) -> bool:
    """Whether this proposal hands its window to its type and no other.

    Mirrors the engine's own reading (``_is_exclusive_window``): only
    working_hours defines a window to claim, and only a type-scoped rule
    has another type to exclude. An exclusivity flag anywhere else names
    nothing the engine would act on, so it is ignored rather than shown
    as a rule the therapist did not get.
    """
    return (
        raw.get("type_exclusive") is True
        and rule_type == "working_hours"
        and appointment_type_id is not None
    )


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

    def parse(
        self,
        text: str,
        reference_date: date | None = None,
        appointment_types: Sequence[AppointmentType] = (),
    ) -> AvailabilityParseResult:
        """Propose rules for ``text``, scoped to ``appointment_types`` if named.

        ``appointment_types`` is the practice's own list, and the only
        source of a type a proposal may be bound to.
        """
        logger.info(
            "Availability parse request: %d chars, %d appointment type(s)",
            len(text),
            len(appointment_types),
        )
        try:
            completion = self._llm_gateway.complete_structured(
                model=self._resolve_model(),
                system_prompt=_SYSTEM_PROMPT
                + "\n\n"
                + _appointment_types_prompt(appointment_types),
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

        result = self._coerce(
            completion.data, reference_date, _index_appointment_types(appointment_types)
        )
        logger.info(
            "Availability parse result: %d proposal(s) [%s]",
            len(result.proposals),
            ",".join(p.rule_type for p in result.proposals),
        )
        return result

    def _coerce(
        self,
        data: dict[str, Any],
        reference_date: date | None,
        type_index: dict[str, str],
    ) -> AvailabilityParseResult:
        raw_proposals = data.get("proposals")
        if not isinstance(raw_proposals, list):
            raw_proposals = []

        proposals: list[ProposedRule] = []
        for raw in raw_proposals:
            try:
                proposal = (
                    self._coerce_one(raw, reference_date, type_index)
                    if isinstance(raw, dict)
                    else None
                )
            except _UnknownAppointmentTypeError as exc:
                # A type the practice does not have is a question, not a
                # rule: binding nothing would silently widen the rule to
                # every kind of appointment.
                return AvailabilityParseResult(
                    could_not_parse=_UNKNOWN_TYPE_COULD_NOT_PARSE.format(name=exc.name),
                    refusal_reason="unknown_appointment_type",
                )
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

    def _coerce_one(
        self,
        raw: dict[str, Any],
        reference_date: date | None,
        type_index: dict[str, str],
    ) -> ProposedRule | None:
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
        appointment_type_id = _resolve_appointment_type(raw.get("appointment_type"), type_index)
        return ProposedRule(
            rule_type=rule_type,
            enforcement=enforcement,
            params=params,
            human_summary=human_summary,
            confidence=_coerce_confidence(raw.get("confidence")),
            appointment_type_id=appointment_type_id,
            allow_other_types=not _claims_its_window(rule_type, appointment_type_id, raw),
        )
