# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which of the practice's patients does an outside record mean?

A calendar feed, a calendar import and an archive all arrive with a name, an
email, a code or a pair of initials, and each has to be tied to a chart or
put in front of the clinician as a question. This is the one place that
decides, so every one of them answers the same way.

A client belongs to the practice, not to one clinician, so the candidates are
every live chart in the practice (``practice_client_directory()``), not only
the ones the acting clinician holds a grant on. Otherwise a colleague's client
is invisible here and becomes a second chart. Each answer says which of its
charts the caller can see (``hidden_ids``); a caller never creates a chart or
books anything for one it cannot see, and tells the clinician who does see it.

How far a check looks depends on how strong its evidence is. The first three
are strong, and look across the whole practice: a certain match to a
colleague's client on any of them is that client. The last two rest on a name
alone, and look only at the charts the caller sees. Two clinicians can each
have a "J.A." without either being the other's; a colleague's chart that
matches only by name or initials never makes the caller's own match
uncertain, and never stands in the way of a new client.

The checks run in a fixed order, strongest first:

1. ``remembered`` — someone already said which patient this source's
   identifier means (``patient_source_mappings``). An answer is the
   clinician's own for a feed's client codes and names, and one calendar's
   for a calendar's series, shared by everyone who follows it; the hint says
   which (``scope``), and the identifier is looked up
   by its keyed digest (``identifiers.identifier_digest``).
2. ``name_and_dob`` — exactly one patient in the practice has this name and
   date of birth.
3. ``email`` — exactly one patient in the practice has this email. Some
   systems treat an email as unique to one client; Pablo does not, because
   families often share one address. So an email settles the question only
   when exactly one chart in the whole practice carries it, and it comes after
   name and date of birth: a child's record carrying a parent's email must
   land on the child's chart, and a shared family email must never outrank a
   unique name and date of birth.
4. ``full_name`` — exactly one of the caller's patients has this whole name,
   and no other of theirs shares its first and last word.
5. ``initials`` — exactly one of the caller's patients has these initials.
   Two of theirs sharing initials stay a question; the answer is remembered,
   so it is asked once per series.

A name that agrees only on its first and last word ("Mary Ann Smith" and a
chart for Mary Smith, or "Pablo Bear" and a chart typed as Pablo A / Bear) is
never certain: it may be someone else, so it is offered as a possible match.

A remembered answer always settles the question. If the patient it names is
no longer a live chart, there is no match at all — never a weaker guess in
its place — and the caller asks again.

A remembered answer can also be that the identifier is not a client at all
(a standing staff meeting on a calendar). That comes back as
``evidence="not_a_client"`` with no patient and nothing possible, and callers
skip the record rather than ask about it.

Every check but the first needs exactly one candidate. Two patients who share
a name are never guessed between: the answer is ``possible_ids`` and no match,
and the caller asks. Comparisons ignore case and extra whitespace. Deleted
patients are never candidates — not even through a remembered answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from ..repositories.patient_source_mapping import (
    ANSWER_CLIENT,
    ANSWER_NOT_A_CLIENT,
    PatientSourceMapping,
)
from .identifiers import (
    calendar_scope,
    clinician_scope,
    identifier_digest,
    is_calendar_scope,
    normalize,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ..models.patient import Patient
    from ..repositories.patient import PatientRepository, PracticeClient
    from ..repositories.patient_source_mapping import PatientSourceMappingRepository

Evidence = Literal["remembered", "not_a_client", "email", "name_and_dob", "full_name", "initials"]

#: Evidence that rests on a name alone. Some callers treat it as a question.
NAME_ONLY: frozenset[Evidence] = frozenset({"full_name", "initials"})

#: A full name needs a first and a last part to identify anyone.
_MIN_NAME_PARTS = 2
#: Initials are a first and a last letter.
_INITIALS_LEN = 2


class PatientHint(BaseModel):
    """What an outside record says about who it belongs to."""

    full_name: str | None = None
    initials: str | None = None
    """First and last initial, in any punctuation: ``"J.A."``."""
    email: str | None = None
    date_of_birth: date | None = None
    source: str | None = None
    """Where the record came from: ``"google_calendar"``, ``"simplepractice"``."""
    source_identifier: str | None = None
    """How that source names the client: a series id, a client code, initials."""
    scope: str | None = None
    """Whose remembered answer counts: ``practice``, or one calendar's. See
    ``identifiers``. Without one, nothing remembered is consulted."""


class MatchResult(BaseModel):
    patient_id: str | None = None
    """Set only when the match is certain."""
    possible_ids: list[str] = Field(default_factory=list)
    """Patients it could be, for the clinician to choose between."""
    evidence: Evidence | None = None
    hidden_ids: list[str] = Field(default_factory=list)
    """Which of ``patient_id`` and ``possible_ids`` the caller holds no grant on."""

    @property
    def visible(self) -> bool:
        """Whether the caller can see the certain match (``True`` with none)."""
        return self.patient_id not in self.hidden_ids

    @property
    def visible_possible_ids(self) -> list[str]:
        return [pid for pid in self.possible_ids if pid not in self.hidden_ids]


@dataclass(frozen=True)
class Candidate:
    """The little matching needs to know about one patient."""

    id: str
    first_name: str
    last_name: str
    date_of_birth: date | None = None
    email: str | None = None
    clinician_ids: tuple[str, ...] = ()
    """Who holds a grant on this chart."""
    visible: bool = True
    """Whether the clinician matching holds one of those grants."""

    @property
    def display_name(self) -> str:
        return " ".join(f"{self.first_name} {self.last_name}".split())

    @classmethod
    def from_patient(cls, patient: Patient) -> Candidate:
        return cls(
            id=patient.id,
            first_name=patient.first_name,
            last_name=patient.last_name,
            date_of_birth=_parse_date(patient.date_of_birth),
            email=patient.email,
        )

    @classmethod
    def from_practice_client(cls, client: PracticeClient, user_id: str) -> Candidate:
        return cls(
            id=client.id,
            first_name=client.first_name,
            last_name=client.last_name,
            date_of_birth=client.date_of_birth,
            email=client.email,
            clinician_ids=client.clinician_ids,
            visible=user_id in client.clinician_ids,
        )


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


class MatchContext:
    """The practice's patients and its remembered answers, read once.

    Build one per request or per sync run and reuse it for every record in
    it; the patients are loaded on first use, not per match, and each
    source's answers on first lookup.

    ``main_calendar_id`` is the clinician's main calendar, when the caller
    knows it. Answers this clinician gave before answers had a scope are all
    about that calendar (it was the only one that could be followed or
    imported), so they are adopted into its scope the first time it is read
    here; feed answers are adopted into the clinician's own scope the same way.
    """

    def __init__(
        self,
        load_candidates: Callable[[], Iterable[Candidate]],
        *,
        user_id: str = "",
        mappings: PatientSourceMappingRepository | None = None,
        main_calendar_id: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.main_calendar_id = main_calendar_id
        self._load_candidates = load_candidates
        self._mappings = mappings
        self._candidates: list[Candidate] | None = None
        # (source, scope) -> identifier digest -> the answer as stored
        self._remembered: dict[tuple[str, str], dict[str, PatientSourceMapping]] = {}

    @classmethod
    def for_practice(
        cls,
        user_id: str,
        patient_repo: PatientRepository,
        mappings: PatientSourceMappingRepository,
        *,
        main_calendar_id: str | None = None,
    ) -> MatchContext:
        """Every live chart in the practice, each marked as seen by ``user_id`` or not."""

        def load() -> list[Candidate]:
            return [
                Candidate.from_practice_client(client, user_id)
                for client in patient_repo.practice_directory()
            ]

        return cls(load, user_id=user_id, mappings=mappings, main_calendar_id=main_calendar_id)

    @classmethod
    def over(cls, candidates: Iterable[Candidate]) -> MatchContext:
        """A context over patients already in hand, with nothing remembered."""
        fixed = list(candidates)
        return cls(lambda: fixed)

    @property
    def candidates(self) -> list[Candidate]:
        if self._candidates is None:
            self._candidates = list(self._load_candidates())
        return self._candidates

    def candidate(self, patient_id: str) -> Candidate | None:
        return next((c for c in self.candidates if c.id == patient_id), None)

    def remembered(self, source: str, scope: str) -> dict[str, PatientSourceMapping]:
        """This scope's answers for a source, by identifier digest."""
        key = (source, scope)
        if key not in self._remembered:
            stored: list[PatientSourceMapping] = []
            if self._mappings is not None:
                if self.user_id and self._adopts_into(scope):
                    self._mappings.adopt_legacy(self.user_id, source, scope)
                stored = self._mappings.list_by_source(scope, source)
            self._remembered[key] = {m.identifier_digest: m for m in stored}
        return self._remembered[key]

    def lookup(self, source: str, scope: str, identifier: str) -> PatientSourceMapping | None:
        """The answer on record for this identifier, if any."""
        return self.remembered(source, scope).get(identifier_digest(identifier))

    def _adopts_into(self, scope: str) -> bool:
        """Whether a clinician's old answers for a source belong in this scope.

        The clinician's own, for a feed: an old answer was always theirs, and
        stays theirs. For a calendar, only the main one: every old calendar
        answer was given about it, and moving them onto another calendar would
        carry a "not a client" for Monday 09:00 onto a calendar it was never
        about.
        """
        if scope == clinician_scope(self.user_id):
            return True
        return self.main_calendar_id is not None and scope == calendar_scope(self.main_calendar_id)

    def save(self, mapping: PatientSourceMapping) -> None:
        if self._mappings is None:
            msg = "This context has nowhere to remember a match"
            raise RuntimeError(msg)
        self._mappings.save(mapping)
        self.remembered(mapping.source, mapping.scope)[mapping.identifier_digest] = mapping


def _full_name_matches(
    full_name: str, candidates: list[Candidate]
) -> tuple[list[Candidate], list[Candidate]]:
    """Patients whose name is this one: (whole-name matches, all matches).

    A whole-name match agrees on every word ("Mary Ann Smith" and Mary Ann /
    Smith). The wider set also takes a patient whose first and last name are
    the name's first and last word ("Jane Q Adams" and Jane / Adams), which
    may be the same person or may not.
    """
    wanted = normalize(full_name)
    parts = wanted.split()
    if len(parts) < _MIN_NAME_PARTS:
        return [], []
    whole: list[Candidate] = []
    found: list[Candidate] = []
    for c in candidates:
        first, last = normalize(c.first_name), normalize(c.last_name)
        if normalize(f"{first} {last}") == wanted:
            whole.append(c)
            found.append(c)
        elif _first_and_last_word(first, last) == (parts[0], parts[-1]):
            found.append(c)
    return whole, found


def _first_and_last_word(first_name: str, last_name: str) -> tuple[str, str]:
    """A chart's name as a first and a last word, whatever else was typed.

    A middle name or initial lands in either field ("Pablo A" / "Bear"), and a
    record naming the same person often drops it, so it counts on neither side.
    """
    first = first_name.split()
    last = last_name.split()
    return (first[0] if first else "", last[-1] if last else "")


def same_name_charts(full_name: str, ctx: MatchContext) -> list[Candidate]:
    """The charts the caller sees whose first and last name are this name's.

    Middle names are ignored on both sides. This is what decides whether a
    name on its own can identify one client: two of the caller's charts
    bearing it can't be told apart by it.
    """
    _, found = _full_name_matches(full_name, [c for c in ctx.candidates if c.visible])
    return found


def _initials_matches(initials: str, candidates: list[Candidate]) -> list[Candidate]:
    letters = re.findall(r"[A-Za-z]", initials)
    if len(letters) != _INITIALS_LEN:
        return []
    first, last = (letter.lower() for letter in letters)
    return [
        c
        for c in candidates
        if normalize(c.first_name)[:1] == first and normalize(c.last_name)[:1] == last
    ]


def match_patient(
    hint: PatientHint, ctx: MatchContext, *, name_alone_is_enough: bool = True
) -> MatchResult:
    """The patient this hint means, or the ones it might mean.

    ``name_alone_is_enough=False`` turns a unique name or initials match into
    a question rather than an answer, for callers that merge records and must
    not do it on a name.

    Strong evidence is judged across the whole practice and weak evidence
    among the charts the caller sees; ``hidden_ids`` says which charts named
    in the answer the caller does not see.
    """
    result = _match(hint, ctx, name_alone_is_enough=name_alone_is_enough)
    named = [result.patient_id, *result.possible_ids] if result.patient_id else result.possible_ids
    unseen = {c.id for c in ctx.candidates if not c.visible}
    result.hidden_ids = [pid for pid in named if pid in unseen]
    return result


def _match(hint: PatientHint, ctx: MatchContext, *, name_alone_is_enough: bool) -> MatchResult:
    candidates = ctx.candidates
    live = {c.id for c in candidates}

    if hint.source and hint.source_identifier and hint.scope:
        known = ctx.lookup(hint.source, hint.scope, hint.source_identifier)
        if known is not None and known.answer == ANSWER_NOT_A_CLIENT:
            return MatchResult(evidence="not_a_client")
        if known is not None:
            if known.patient_id in live:
                return MatchResult(patient_id=known.patient_id, evidence="remembered")
            # The remembered patient is no longer a live chart. Falling
            # through would quietly hand the record to whoever else shares
            # the initials or name; ask instead.
            return MatchResult()

    # A name alone is weak evidence, so it is judged among the charts the
    # caller can see: a colleague's same-named client neither blocks nor
    # unsettles the caller's own.
    seen = [c for c in candidates if c.visible]
    _, by_name_in_practice = (
        _full_name_matches(hint.full_name, candidates) if hint.full_name else ([], [])
    )
    whole_name, by_name = _full_name_matches(hint.full_name, seen) if hint.full_name else ([], [])
    by_initials = _initials_matches(hint.initials, seen) if hint.initials else []

    steps: list[tuple[Evidence, list[Candidate]]] = []
    if hint.full_name and hint.date_of_birth is not None:
        steps.append(
            (
                "name_and_dob",
                [c for c in by_name_in_practice if c.date_of_birth == hint.date_of_birth],
            )
        )
    if hint.email and normalize(hint.email):
        wanted = normalize(hint.email)
        steps.append(("email", [c for c in candidates if normalize(c.email) == wanted]))
    if hint.full_name:
        # Certain only when the one name match is a whole-name match.
        steps.append(("full_name", whole_name if len(by_name) == 1 else by_name))
    if hint.initials:
        steps.append(("initials", by_initials))

    for evidence, found in steps:
        if len(found) == 1 and (name_alone_is_enough or evidence not in NAME_ONLY):
            return MatchResult(patient_id=found[0].id, evidence=evidence)

    # The name says who it could be; without one, the first check that
    # found anyone does.
    possible = by_name or by_initials or next((found for _, found in steps if found), [])
    return MatchResult(possible_ids=[c.id for c in possible])


def remember_match(  # noqa: PLR0913 — the answer's parts, each named at the call site
    source: str,
    source_identifier: str,
    patient_id: str,
    ctx: MatchContext,
    *,
    scope: str,
    answered_title: str | None = None,
) -> None:
    """Record that this source's identifier means this patient.

    Idempotent. An identifier already remembered under different case or
    spacing is the same identifier, and its answer is replaced in place.

    ``scope`` is whose answer this is: the clinician's for a feed's client
    code or name, one calendar's for a series (see ``identifiers``).

    ``answered_title`` is the keyed digest of the title the answer was given
    under (``answered_title_digest``), for sources whose identifier can
    outlive the client it named: a provider's series id stays the same when
    the series is handed to someone else, so the answer holds only while the
    title does. Left unset by sources where the identifier is the title.
    """
    _remember(ctx, source, source_identifier, scope, patient_id, ANSWER_CLIENT, answered_title)


def remember_not_a_client(
    source: str, source_identifier: str, ctx: MatchContext, *, scope: str
) -> None:
    """Record that this source's identifier is not a client, so it is never asked about.

    Idempotent, and replaces a client answer for the same identifier.
    """
    _remember(ctx, source, source_identifier, scope, None, ANSWER_NOT_A_CLIENT, None)


def _remember(  # noqa: PLR0913 — the answer's parts, each named at the call site
    ctx: MatchContext,
    source: str,
    source_identifier: str,
    scope: str,
    patient_id: str | None,
    answer: str,
    answered_title: str | None,
) -> None:
    existing = ctx.lookup(source, scope, source_identifier)
    if existing is not None and (existing.answer, existing.patient_id, existing.answered_title) == (
        answer,
        patient_id,
        answered_title,
    ):
        return
    ctx.save(
        PatientSourceMapping(
            scope=scope,
            source=source,
            identifier_digest=identifier_digest(source_identifier),
            patient_id=patient_id,
            answered_by_user_id=ctx.user_id,
            answer=answer,
            answered_title=answered_title,
            # On a personal calendar the answerer's session is the one booked.
            session_clinician_user_id=ctx.user_id if is_calendar_scope(scope) else None,
        )
    )


__all__ = [
    "NAME_ONLY",
    "Candidate",
    "Evidence",
    "MatchContext",
    "MatchResult",
    "PatientHint",
    "match_patient",
    "normalize",
    "remember_match",
    "remember_not_a_client",
    "same_name_charts",
]
