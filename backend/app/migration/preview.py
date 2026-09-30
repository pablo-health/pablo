# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""What an archive would become, and what needs a decision first.

A preview is built from three inputs and nothing else: the archive as read,
the attribution of each record to a client, and a snapshot of the ledger
(what earlier runs already landed). It is a plain dictionary — it is stored
on the run row and returned to the screen as it is — and it contains handles,
titles and dates, never a note body or a message.

For every record it says one of: ``new`` (never seen), ``unchanged`` (seen,
same bytes), ``changed`` (seen, different bytes, safe to update), ``conflict``
(seen and changed in the source, but edited here since — left alone). It
also says which records cannot land in this version and why, and lists the
questions the practice must answer before apply: which client an
unattributed record belongs to, whether a client is the same person as an
existing patient, and which user each exported provider is.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..patients.matching import Candidate, MatchContext, PatientHint, match_patient
from .attribution import ArchiveAttribution, attribute_archive

if TYPE_CHECKING:
    from .ledger import LedgerEntry
    from .readers.simplepractice import ContactCard, SimplePracticeArchive

#: Record types this version lands. Everything else is reported.
LANDABLE = {"contact", "note", "questionnaire", "thread", "upload"}
_BILLING_LABELS = {"invoice": "Invoice", "statement": "Statement", "superbill": "Superbill"}
CANNOT_LAND_REASONS = {
    "billing": (
        "Invoices, statements and superbills land in a later version; "
        "balances are not carried over yet."
    ),
    "non_client_contact": (
        "Contacts who are not clients (family, emergency contacts) land in a later version."
    ),
    "diagnosis_codes": (
        "Diagnosis codes are kept on each imported note but not yet as a chart diagnosis list."
    ),
    "billing_codes": (
        "Billing codes are kept on each imported note; visits do not carry a service code yet."
    ),
    "unrecognized": "Files that are not part of an export layout this version knows.",
    "upload_type": "Documents that aren't PDFs or images stay in your old system for now.",
}

#: Upload file types the chart stores (the patient-documents whitelist), by
#: extension. Anything else is reported, not landed.
STORED_UPLOAD_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


#: The little the preview needs to know about a patient already here.
ExistingPatient = Candidate


@dataclass(frozen=True)
class PreviewInputs:
    """Everything a preview is built from, besides the archive itself."""

    ledger: dict[tuple[str, str], LedgerEntry] = field(default_factory=dict)
    existing_patients: list[ExistingPatient] = field(default_factory=list)
    edited_targets: set[tuple[str, str]] = field(default_factory=set)
    scope: str = "both"


def _label(record_type: str, record: Any) -> tuple[str, str | None]:
    """A short human label and an ISO date for a record, with no content."""
    if record_type == "note":
        when = record.appointment.on if record.appointment else None
        when = when or (record.noted_at.date() if record.noted_at else None)
        when = when or (record.created_at.date() if record.created_at else None)
        return record.title, when.isoformat() if when else None
    if record_type == "questionnaire":
        when = record.completed_at.date() if record.completed_at else None
        return record.title, when.isoformat() if when else None
    if record_type == "billing":
        kind = _BILLING_LABELS[record.kind]
        label = f"{kind} #{record.number}" if record.number else kind
        return label, record.issued_on.isoformat() if record.issued_on else None
    if record_type == "thread":
        first = next((m.sent_at for m in record.messages if m.sent_at), None)
        return f"Messages ({len(record.messages)})", first.date().isoformat() if first else None
    if record_type == "upload":
        return record.original_filename, None
    return record_type, None


def _delta_state(
    entry: LedgerEntry | None, digest: str, edited_targets: set[tuple[str, str]]
) -> str:
    if entry is None or entry.state == "undone":
        return "new"
    if entry.source_digest == digest:
        return "unchanged"
    if (entry.target_table, entry.target_id) in edited_targets:
        return "conflict"
    return "changed"


def _match_existing(
    card: ContactCard, ctx: MatchContext
) -> tuple[str | None, str | None, list[str]]:
    """(patient id when certain, evidence, possible-duplicate ids).

    A name alone never merges a client into an existing chart here: a
    same-named patient is put to the practice as a possible duplicate.
    """
    hint = PatientHint(
        full_name=f"{card.given_name} {card.family_name}",
        email=card.email,
        date_of_birth=card.birthday,
    )
    result = match_patient(hint, ctx, name_alone_is_enough=False)
    return result.patient_id, result.evidence, result.possible_ids


class _Builder:
    def __init__(
        self, archive: SimplePracticeArchive, inputs: PreviewInputs, att: ArchiveAttribution
    ) -> None:
        self.archive = archive
        self.inputs = inputs
        self.att = att
        self.records: list[dict[str, Any]] = []
        self.counts: dict[str, Counter[str]] = defaultdict(Counter)

    def add(self, record_type: str, record: Any, *, landable: bool, reason: str | None) -> None:
        a = self.att.attributions.get((record_type, record.source_id))
        entry = self.inputs.ledger.get((record_type, record.source_id))
        state = _delta_state(entry, record.digest, self.inputs.edited_targets)
        label, when = _label(record_type, record)
        self.records.append(
            {
                "record_type": record_type,
                "source_id": record.source_id,
                "path": record.path,
                "kind": getattr(record, "kind", record_type),
                "label": label,
                "when": when,
                "card_id": a.card_id if a else None,
                "evidence": a.evidence if a else None,
                "name_disagrees": a.name_disagrees if a else False,
                "candidates": list(a.candidates) if a else [],
                "state": state,
                "landable": landable,
                "reason": reason,
            }
        )
        self.counts[record_type][state] += 1
        if a is not None and not a.resolved and landable:
            self.counts[record_type]["unresolved"] += 1

    def records_section(self) -> None:
        for note in self.archive.notes:
            self.add("note", note, landable=True, reason=None)
        for q in self.archive.questionnaires:
            self.add("questionnaire", q, landable=True, reason=None)
        for t in self.archive.threads:
            self.add("thread", t, landable=True, reason=None)
        for u in self.archive.uploads:
            stored = Path(u.original_filename).suffix.lower() in STORED_UPLOAD_TYPES
            self.add(
                "upload",
                u,
                landable=stored,
                reason=None if stored else CANNOT_LAND_REASONS["upload_type"],
            )
        for b in self.archive.billing:
            self.add("billing", b, landable=False, reason=CANNOT_LAND_REASONS["billing"])

    def clients_section(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        clients: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        existing = MatchContext.over(self.inputs.existing_patients)
        for card in self.att.clients:
            entry = self.inputs.ledger.get(("contact", card.source_id))
            state = _delta_state(entry, card.digest, self.inputs.edited_targets)
            matched_id, evidence, possible = _match_existing(card, existing)
            if entry is not None and entry.state != "undone":
                matched_id, evidence, possible = entry.target_id, "ledger", []
            address = card.address
            clients.append(
                {
                    "card_id": card.source_id,
                    "display_name": card.display_name,
                    "folder_name": card.folder_name,
                    "birthday": card.birthday.isoformat() if card.birthday else None,
                    "email": card.email,
                    "phone": card.phone,
                    "address": (
                        {
                            "street": address.street,
                            "city": address.city,
                            "state": address.state,
                            "postal_code": address.postal_code,
                        }
                        if address
                        else None
                    ),
                    "state": state,
                    "existing_patient_id": matched_id,
                    "match_evidence": evidence,
                    "possible_duplicates": possible,
                }
            )
            self.counts["contact"][state] += 1
            if matched_id is None and possible:
                duplicates.append({"card_id": card.source_id, "possible_duplicates": possible})
                self.counts["contact"]["unresolved"] += 1
        return clients, duplicates

    def same_name_section(self) -> list[dict[str, Any]]:
        groups = []
        for folder, cards in self.att.same_name_groups.items():
            ids = {c.source_id for c in cards}
            records = [
                {"record_type": r["record_type"], "source_id": r["source_id"]}
                for r in self.records
                if r["landable"] and r["card_id"] is None and set(r["candidates"]) == ids
            ]
            if records:
                groups.append(
                    {"folder_name": folder, "candidates": sorted(ids), "records": records}
                )
        return groups

    def cannot_land_section(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        archive, att = self.archive, self.att
        if archive.billing:
            out.append(_cannot("billing", len(archive.billing)))
        if att.non_client_contacts:
            out.append(_cannot("non_client_contact", len(att.non_client_contacts)))
        diagnosed = sum(1 for n in archive.notes if n.diagnoses)
        if diagnosed:
            out.append(_cannot("diagnosis_codes", diagnosed))
        coded = sum(1 for n in archive.notes if n.appointment and n.appointment.billing_code)
        if coded:
            out.append(_cannot("billing_codes", coded))
        unstored = sum(
            1 for r in self.records if r["record_type"] == "upload" and not r["landable"]
        )
        if unstored:
            out.append(_cannot("upload_type", unstored))
        out.extend(
            {"what": "unreadable", "count": 1, "reason": f"{rel}: {why}"}
            for rel, why in archive.unreadable
        )
        if archive.unrecognized:
            out.append(_cannot("unrecognized", len(archive.unrecognized)))
        return out


def _cannot(what: str, count: int) -> dict[str, Any]:
    return {"what": what, "count": count, "reason": CANNOT_LAND_REASONS[what]}


def build_preview(
    archive: SimplePracticeArchive,
    inputs: PreviewInputs | None = None,
    *,
    attribution: ArchiveAttribution | None = None,
) -> dict[str, Any]:
    inputs = inputs or PreviewInputs()
    b = _Builder(archive, inputs, attribution or attribute_archive(archive))
    b.records_section()
    clients, duplicates = b.clients_section()
    return {
        "source_system": "simplepractice",
        "scope": inputs.scope,
        "clients": clients,
        "non_client_contacts": [
            {"card_id": c.source_id, "display_name": c.display_name}
            for c in b.att.non_client_contacts
        ],
        "providers": list(archive.provider_names),
        "records": b.records,
        "counts": {k: dict(v) for k, v in b.counts.items()},
        "cannot_land": b.cannot_land_section(),
        "questions": {
            "same_name": b.same_name_section(),
            "duplicates": duplicates,
            "providers": [{"name": p} for p in archive.provider_names],
        },
        "practice": _practice_proposals(archive),
    }


def _practice_proposals(archive: SimplePracticeArchive) -> dict[str, Any]:
    """What the archive says about the practice itself, for confirmation."""
    kinds = Counter(
        (n.appointment.kind, n.appointment.duration_minutes)
        for n in archive.notes
        if n.appointment and n.kind == "progress"
    )
    visit = kinds.most_common(1)[0][0] if kinds else None
    amounts = Counter(
        line.fee_cents for b in archive.billing for line in b.service_lines if line.fee_cents
    )
    amounts.update(b.total_cents for b in archive.billing if b.kind == "invoice" and b.total_cents)
    rate = amounts.most_common(1)[0][0] if amounts else None
    providers = list(archive.provider_names)
    return {
        "proposals": {
            "provider_name": providers[0] if len(providers) == 1 else None,
            "visit_kind": visit[0] if visit else None,
            "visit_minutes": visit[1] if visit else None,
            "rate_cents": rate,
        },
        "not_in_export": ["npi", "tax_id", "practice_address", "practice_phone"],
    }


def decisions_complete(preview: dict[str, Any], decisions: dict[str, Any]) -> list[str]:
    """Which questions are still unanswered; empty means apply may run."""
    missing: list[str] = []
    assignments = decisions.get("assignments", {})
    for group in preview["questions"]["same_name"]:
        for r in group["records"]:
            key = f"{r['record_type']}:{r['source_id']}"
            if key not in assignments:
                missing.append(f"assignment for {key}")
    dup_answers = decisions.get("duplicates", {})
    missing.extend(
        f"duplicate decision for client {d['card_id']}"
        for d in preview["questions"]["duplicates"]
        if d["card_id"] not in dup_answers
    )
    provider_map = decisions.get("providers", {})
    missing.extend(
        f"user for provider {p['name']}"
        for p in preview["questions"]["providers"]
        if not provider_map.get(p["name"])
    )
    return missing


__all__ = [
    "CANNOT_LAND_REASONS",
    "LANDABLE",
    "STORED_UPLOAD_TYPES",
    "ExistingPatient",
    "PreviewInputs",
    "build_preview",
    "decisions_complete",
]
