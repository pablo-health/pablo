# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Which client does each record in an archive belong to?

The source system keys every folder by first and last name, so two clients
with one name share every folder and nothing in a path says whose record a
file is. This module decides, on evidence only, and says so when it cannot.

Evidence, strongest first:

* ``dob`` — the record's ``DOB:`` line matches exactly one candidate card's
  birthday. Wins over the name, because administrative notes print first
  and last name only and would otherwise be filed on the wrong client.
* ``email`` / ``phone`` — a billing document's client block matches exactly
  one card; the source system keeps one client per email.
* ``display_name`` — the record's display name (the ``Client:`` line, a
  message sender, or the message file's name) matches exactly one card's
  file-name display name — the only place the export writes a middle
  initial.
* ``only_candidate`` — one card owns the folder, so there is nothing to
  decide. A DOB that contradicts that one card is still reported.

Anything else is **unresolved** and goes in front of the practice to
assign. Nothing here guesses, splits evenly, or takes the first card.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .readers.simplepractice import (
        BillingDocument,
        ContactCard,
        MessageThread,
        NoteRecord,
        QuestionnaireRecord,
        SimplePracticeArchive,
        Upload,
    )

#: The sender the source system uses for its own lines in a message log.
SYSTEM_SENDER = "System"


@dataclass(frozen=True)
class Attribution:
    """Where one record lands, or why it cannot yet."""

    record_type: str
    source_id: str
    path: str
    #: Card source ids that could own this record (same folder or same name).
    candidates: tuple[str, ...]
    #: The card the record is attributed to, or ``None`` when unresolved.
    card_id: str | None
    #: Which tier decided it: ``dob``, ``email``, ``phone``, ``display_name``,
    #: ``only_candidate``; ``None`` when unresolved.
    evidence: str | None
    #: The record's own name disagreed with the deciding evidence — kept so
    #: the review screen can say "the DOB line decided this".
    name_disagrees: bool = False

    @property
    def resolved(self) -> bool:
        return self.card_id is not None


@dataclass(frozen=True)
class ArchiveAttribution:
    #: Every card that owns at least one folder or thread — the clients.
    clients: tuple[ContactCard, ...]
    #: Cards that own nothing: emergency contacts, family, referrals.
    non_client_contacts: tuple[ContactCard, ...]
    attributions: dict[tuple[str, str], Attribution] = field(default_factory=dict)

    def by_type(self, record_type: str) -> list[Attribution]:
        return [a for (t, _), a in self.attributions.items() if t == record_type]

    @property
    def unresolved(self) -> list[Attribution]:
        return [a for a in self.attributions.values() if not a.resolved]

    @property
    def same_name_groups(self) -> dict[str, tuple[ContactCard, ...]]:
        """Folder names owned by more than one client card."""
        groups: dict[str, list[ContactCard]] = defaultdict(list)
        for card in self.clients:
            groups[card.folder_name].append(card)
        return {k: tuple(v) for k, v in groups.items() if len(v) > 1}


def _folder_of(path: str) -> str:
    return path.split("/")[1] if "/" in path else ""


def _first_last(display_name: str) -> str:
    """The folder name the export would use for a display name: first and
    last token, dropping a middle initial ("Pablo A. Bear" -> "Pablo Bear")."""
    parts = display_name.split()
    if len(parts) > _FIRST_AND_LAST:
        return f"{parts[0]} {parts[-1]}"
    return display_name


#: A display name of exactly first and last name has this many tokens.
_FIRST_AND_LAST = 2


def identify_clients(archive: SimplePracticeArchive) -> tuple[list[ContactCard], list[ContactCard]]:
    """Split cards into clients (own a folder or a message thread) and the rest."""
    folders = set(archive.client_folders)
    senders = {p for t in archive.threads for p in t.participants if p != SYSTEM_SENDER}
    clients: list[ContactCard] = []
    others: list[ContactCard] = []
    for card in archive.contacts:
        if card.folder_name in folders or card.display_name in senders:
            clients.append(card)
        else:
            others.append(card)
    return clients, others


@dataclass(frozen=True)
class _Evidence:
    """What a record says about its own client."""

    display_name: str | None = None
    dob: object | None = None
    email: str | None = None
    phone: str | None = None


def _digits(value: str | None) -> str:
    return "".join(ch for ch in value or "" if ch.isdigit())


def _match(candidates: list[ContactCard], ev: _Evidence) -> tuple[ContactCard, str] | None:
    """The one card the evidence names, with the tier that named it."""
    if len(candidates) == 1:
        only = candidates[0]
        if ev.dob is not None and only.birthday is not None and only.birthday != ev.dob:
            # One card, but the record says a different birthday: the archive
            # contradicts itself, which is a question, not a landing.
            return None
        return only, "only_candidate"
    tiers: list[tuple[str, list[ContactCard]]] = []
    if ev.dob is not None:
        tiers.append(("dob", [c for c in candidates if c.birthday == ev.dob]))
    if ev.email:
        wanted = ev.email.lower()
        tiers.append(("email", [c for c in candidates if (c.email or "").lower() == wanted]))
    if ev.phone:
        wanted = _digits(ev.phone)
        tiers.append(("phone", [c for c in candidates if c.phone and _digits(c.phone) == wanted]))
    if ev.display_name:
        tiers.append(("display_name", [c for c in candidates if c.display_name == ev.display_name]))
    for evidence, matched in tiers:
        if len(matched) == 1:
            return matched[0], evidence
    return None


def _decide(
    record_type: str, source_id: str, path: str, candidates: list[ContactCard], ev: _Evidence
) -> Attribution:
    ids = tuple(c.source_id for c in candidates)
    by_name = [c for c in candidates if ev.display_name and c.display_name == ev.display_name]
    name_pick = by_name[0].source_id if len(by_name) == 1 else None
    picked = _match(candidates, ev) if candidates else None
    if picked is None:
        # A single candidate whose birthday contradicts the record is the one
        # unresolved case where the name is worth flagging too.
        contradicted = len(candidates) == 1 and ev.dob is not None
        return Attribution(record_type, source_id, path, ids, None, None, contradicted)
    card, evidence = picked
    disagrees = name_pick is not None and name_pick != card.source_id
    return Attribution(record_type, source_id, path, ids, card.source_id, evidence, disagrees)


def attribute_archive(archive: SimplePracticeArchive) -> ArchiveAttribution:
    clients, others = identify_clients(archive)
    by_folder: dict[str, list[ContactCard]] = defaultdict(list)
    for card in clients:
        by_folder[card.folder_name].append(card)
    out: dict[tuple[str, str], Attribution] = {}

    def put(a: Attribution) -> None:
        out[(a.record_type, a.source_id)] = a

    for note in archive.notes:
        put(_attribute_note(note, by_folder[_folder_of(note.path)]))
    for q in archive.questionnaires:
        put(_attribute_questionnaire(q, by_folder[_folder_of(q.path)]))
    for b in archive.billing:
        put(_attribute_billing(b, by_folder[b.client_folder]))
    for t in archive.threads:
        put(_attribute_thread(t, clients))
    for u in archive.uploads:
        put(_attribute_upload(u, by_folder[u.client_folder]))
    return ArchiveAttribution(
        clients=tuple(clients), non_client_contacts=tuple(others), attributions=out
    )


def _attribute_note(note: NoteRecord, candidates: list[ContactCard]) -> Attribution:
    ev = _Evidence(display_name=note.client_display_name, dob=note.client_dob)
    return _decide("note", note.source_id, note.path, candidates, ev)


def _attribute_questionnaire(q: QuestionnaireRecord, candidates: list[ContactCard]) -> Attribution:
    ev = _Evidence(display_name=q.client_display_name, dob=q.client_dob)
    return _decide("questionnaire", q.source_id, q.path, candidates, ev)


def _attribute_billing(b: BillingDocument, candidates: list[ContactCard]) -> Attribution:
    ev = _Evidence(
        display_name=b.client_display_name,
        dob=b.client_dob,
        email=b.client_email,
        phone=b.client_phone,
    )
    return _decide("billing", b.source_id, b.path, candidates, ev)


def _attribute_thread(t: MessageThread, clients: list[ContactCard]) -> Attribution:
    # A thread has no folder; its client is whichever participant is not the
    # source system. The candidates are every client who shares that
    # participant's first and last name, so a middle initial in the sender
    # line is what decides between same-named clients.
    names = [p for p in t.participants if p != SYSTEM_SENDER]
    display = names[0] if len(names) == 1 else None
    folder_names = {_first_last(n) for n in names}
    candidates = [c for c in clients if c.folder_name in folder_names or c.display_name in names]
    return _decide("thread", t.source_id, t.path, candidates, _Evidence(display_name=display))


def _attribute_upload(u: Upload, candidates: list[ContactCard]) -> Attribution:
    # An upload carries nothing but its folder: one candidate lands, more do not.
    return _decide("upload", u.source_id, u.path, candidates, _Evidence())


__all__ = ["ArchiveAttribution", "Attribution", "attribute_archive", "identify_clients"]
