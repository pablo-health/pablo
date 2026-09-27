# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Land a previewed archive in the chart, and take it back out again.

Every write goes through the repositories and services the application
uses, on a tenant-armed session, so row-level security, grants and deletion
semantics are exactly what a clinician's own actions get.

What lands, and how:

* A client card becomes a patient — merged into an existing patient when
  the preview matched one (or the practice said so), otherwise created with
  ``origin="simplepractice"`` so a later duplicate review knows where it
  came from.
* A progress note becomes a finalized imported session with a narrative
  note whose body is the export's text, verbatim, dated from the note's
  appointment line, beside a completed appointment for the visit. A chart,
  administrative or treatment-plan note becomes a finalized standalone
  narrative note. A psychotherapy note becomes a restricted standalone note
  whose author is the mapped provider — the restricted class already keeps
  it out of exports, other clinicians' views and every model path. Signer
  and signing time travel as provenance on the note; the signing IP address
  is never read into it.
* A questionnaire becomes an outcome measure with its item scores.
* A message log becomes a thread with each message at its sent time.
* An upload becomes a chart document through the same storage path a
  browser upload takes; where no document storage is configured, it is
  reported instead.

The ledger records every landing by the source's own id, which is what
makes a second run exact and undo complete.
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from ..db.models import (
    AppointmentRow,
    NoteRow,
    OutcomeMeasureRow,
    PatientMessageRow,
    PatientMessageThreadRow,
    PatientRow,
    TherapySessionRow,
)
from ..models.enums import OutcomeMeasureSource, SessionSource, SessionStatus, TranscriptFormat
from ..models.patient import Patient
from ..models.patient_document import DocumentCategory
from ..models.patient_message import (
    SENDER_CLINICIAN,
    SENDER_PATIENT,
    PatientMessage,
    PatientMessageThread,
)
from ..models.session import TherapySession
from ..models.transcript import Transcript
from ..outcome_measures.schemas import CreateOutcomeMeasureRequest
from ..outcome_measures.service import OutcomeMeasureService
from ..repositories import (
    get_appointment_repository,
    get_appointment_type_repository,
    get_notes_repository,
    get_outcome_measure_repository,
    get_patient_document_repository,
    get_patient_message_repository,
    get_patient_repository,
    get_session_repository,
)
from ..scheduling_engine.models.appointment import Appointment, AppointmentStatus
from ..scheduling_engine.models.appointment_type import AppointmentType
from ..services.note_service import NoteService
from ..services.patient_documents_service import PatientDocumentError, PatientDocumentsService
from ..utcnow import utc_now
from .attribution import SYSTEM_SENDER
from .ledger import Landed, record_landed, records_for_run
from .readers.simplepractice import SOURCE_SYSTEM

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from ..settings import Settings
    from .readers.simplepractice import (
        ContactCard,
        MessageThread,
        NoteRecord,
        QuestionnaireRecord,
        SimplePracticeArchive,
        Upload,
    )

#: ``patients.origin`` is 20 characters wide.
PATIENT_ORIGIN = "simplepractice"
_STATE_LEN = 2
_POSTAL_LEN = 10
_UPLOAD_MIME = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}
_SKIP = "skip"
#: Undo removes children before the patients whose soft delete cascades.
_UNDO_ORDER = {
    "upload": 0,
    "thread": 1,
    "questionnaire": 2,
    "note": 3,
    "appointment": 4,
    "appointment_type": 5,
    "contact": 6,
}
_EDITABLE_TABLES: dict[str, Any] = {
    "notes": NoteRow,
    "patients": PatientRow,
    "therapy_sessions": TherapySessionRow,
    "appointments": AppointmentRow,
    "outcome_measures": OutcomeMeasureRow,
}


class ApplyRefusedError(ValueError):
    """Apply cannot run as asked; the message says why, in words for the screen."""


def _count_table() -> dict[str, Counter[str]]:
    return defaultdict(Counter)


@dataclass
class ApplyReport:
    counts: dict[str, Counter[str]] = field(default_factory=_count_table)
    not_landed: list[dict[str, Any]] = field(default_factory=list)
    patients: dict[str, str] = field(default_factory=dict)  # card id -> patient id

    def as_dict(self) -> dict[str, Any]:
        return {
            "counts": {k: dict(v) for k, v in self.counts.items()},
            "not_landed": list(self.not_landed),
            "patients": dict(self.patients),
        }

    def miss(self, record_type: str, key: str, reason: str, *, bucket: str = "unresolved") -> None:
        self.not_landed.append({"key": key, "reason": reason})
        self.counts[record_type][bucket] += 1


@dataclass
class _Run:
    """What one apply call carries through every step."""

    run_id: str
    archive: SimplePracticeArchive
    decisions: dict[str, Any]
    providers: dict[str, str]
    landable: dict[tuple[str, str], dict[str, Any]]
    report: ApplyReport
    visits: dict[tuple[str, str], str] = field(default_factory=dict)


def _key(record_type: str, source_id: str) -> str:
    return f"{record_type}:{source_id}"


def _trim(value: str | None, width: int) -> str | None:
    return value[:width] if value else None


class ArchiveApplier:
    """Lands one previewed archive for one practice, as one user."""

    def __init__(
        self,
        *,
        session: Session,
        settings: Settings,
        user_id: str,
        practice_id: str | None,
        archive_dir: Path,
    ) -> None:
        self._session = session
        self._user_id = user_id
        self._archive_dir = archive_dir
        self._patients = get_patient_repository()
        self._sessions = get_session_repository()
        self._notes_repo = get_notes_repository()
        self._notes = NoteService(self._notes_repo)
        self._appointments = get_appointment_repository()
        self._measures = OutcomeMeasureService(get_outcome_measure_repository())
        self._messages = get_patient_message_repository()
        self._documents = PatientDocumentsService(
            repo=get_patient_document_repository(), settings=settings, tenant_id=practice_id
        )

    # ------------------------------------------------------------------ apply

    def apply(
        self,
        *,
        run_id: str,
        archive: SimplePracticeArchive,
        preview: dict[str, Any],
        decisions: dict[str, Any],
    ) -> ApplyReport:
        run = _Run(
            run_id=run_id,
            archive=archive,
            decisions=decisions,
            providers=self._provider_map(decisions),
            landable={
                (r["record_type"], r["source_id"]): r for r in preview["records"] if r["landable"]
            },
            report=ApplyReport(),
        )
        scope = preview.get("scope", "both")
        if scope in {"patients", "both"}:
            cards = {c.source_id: c for c in archive.contacts}
            for client in preview["clients"]:
                self._land_client(run, cards[client["card_id"]], client)
            self._land_notes(run)
            self._land_questionnaires(run)
            self._land_threads(run)
            self._land_uploads(run)
        if scope in {"practice", "both"}:
            self._land_practice(run, preview)
        return run.report

    def _provider_map(self, decisions: dict[str, Any]) -> dict[str, str]:
        mapped = {}
        for name, user in (decisions.get("providers") or {}).items():
            mapped[name] = self._user_id if user in {"me", None, ""} else str(user)
        return mapped

    def _author_for(self, run: _Run, provider_name: str | None) -> str:
        if provider_name and provider_name in run.providers:
            return run.providers[provider_name]
        return self._user_id

    def _ledger(self, run: _Run, landed: Landed) -> None:
        record_landed(self._session, SOURCE_SYSTEM, run.run_id, landed)

    def _target_for(self, run: _Run, record: dict[str, Any]) -> str | None:
        """The patient id a record lands on, or ``None`` with the reason reported."""
        rtype = record["record_type"]
        key = _key(rtype, record["source_id"])
        card_id = record["card_id"] or (run.decisions.get("assignments") or {}).get(key)
        if card_id is None:
            run.report.miss(rtype, key, "not assigned to a client")
            return None
        if card_id == _SKIP:
            run.report.counts[rtype]["skipped"] += 1
            return None
        patient_id = run.report.patients.get(card_id)
        if patient_id is None:
            run.report.miss(rtype, key, "its client did not land")
        return patient_id

    def _landable(self, run: _Run, record_type: str, source_id: str) -> dict[str, Any] | None:
        """The preview record if it should land now; counts and reports it otherwise."""
        record = run.landable.get((record_type, source_id))
        if record is None:
            return None
        state = record["state"]
        if state == "unchanged":
            run.report.counts[record_type]["unchanged"] += 1
            return None
        if state == "conflict":
            run.report.miss(
                record_type,
                _key(record_type, source_id),
                "changed in the source but edited here since; left as it is",
                bucket="conflict",
            )
            return None
        return record

    # ------------------------------------------------------------------ clients

    def _land_client(self, run: _Run, card: ContactCard, client: dict[str, Any]) -> None:
        card_id = card.source_id
        key = _key("contact", card_id)
        existing = client.get("existing_patient_id")
        answer = (run.decisions.get("duplicates") or {}).get(card_id)
        if existing is None and client.get("possible_duplicates"):
            if not answer:
                run.report.miss("contact", key, "duplicate not decided")
                return
            if answer.startswith("merge:"):
                existing = answer.removeprefix("merge:")
        if existing:
            patient = self._patients.get(existing, self._user_id)
            if patient is None:
                run.report.miss("contact", key, "matched patient not found")
                return
            run.report.patients[card_id] = patient.id
            run.report.counts["contact"]["merged"] += 1
            self._ledger(run, Landed("contact", card_id, "patients", patient.id, card.digest))
            return
        now = utc_now()
        address = card.address
        patient = Patient(
            id=str(uuid.uuid4()),
            first_name=card.given_name or card.formatted_name.split(" ")[0],
            last_name=card.family_name or card.formatted_name.split(" ")[-1],
            created_at=now,
            updated_at=now,
            email=card.email,
            phone=card.phone,
            date_of_birth=card.birthday.isoformat() if card.birthday else None,
            origin=PATIENT_ORIGIN,
            address_line1=address.street or None if address else None,
            city=address.city or None if address else None,
            state=_trim(address.state, _STATE_LEN) if address else None,
            postal_code=_trim(address.postal_code, _POSTAL_LEN) if address else None,
        )
        created = self._patients.create(patient, self._user_id)
        run.report.patients[card_id] = created.id
        run.report.counts["contact"]["created"] += 1
        self._ledger(run, Landed("contact", card_id, "patients", created.id, card.digest))

    # ------------------------------------------------------------------ notes

    def _land_notes(self, run: _Run) -> None:
        for note in run.archive.notes:
            record = self._landable(run, "note", note.source_id)
            if record is None:
                continue
            patient_id = self._target_for(run, record)
            if patient_id is None:
                continue
            author = self._author_for(run, note.provider_name)
            if note.kind == "progress" and note.appointment is not None:
                self._visit_for(run, note, patient_id, author)
                note_id = self._land_session_note(note, patient_id, author)
            elif note.kind == "psychotherapy":
                note_id = self._land_standalone(note, patient_id, author, "psychotherapy")
            else:
                note_id = self._land_standalone(note, patient_id, author, "narrative")
            self._ledger(run, Landed("note", note.source_id, "notes", note_id, note.digest))
            run.report.counts["note"][record["state"]] += 1

    def _visit_for(self, run: _Run, note: NoteRecord, patient_id: str, user_id: str) -> str | None:
        """The completed appointment a visit's notes hang off, created once."""
        info = note.appointment
        if info is None:
            return None
        starts = info.starts_at
        visit_key = (patient_id, starts.isoformat())
        if visit_key in run.visits:
            return run.visits[visit_key]
        existing = self._session.execute(
            select(AppointmentRow.id).where(
                AppointmentRow.patient_id == patient_id,
                AppointmentRow.start_at == starts,
                AppointmentRow.status != AppointmentStatus.CANCELLED.value,
            )
        ).scalar_one_or_none()
        if existing:
            run.visits[visit_key] = existing
            return existing
        appointment = Appointment(
            id=str(uuid.uuid4()),
            user_id=user_id,
            patient_id=patient_id,
            title=f"{info.kind} appointment",
            start_at=starts,
            end_at=info.ends_at,
            duration_minutes=info.duration_minutes,
            status=AppointmentStatus.COMPLETED.value,
            session_type=info.kind.lower(),
            note_type="narrative",
            created_at=utc_now(),
        )
        created = self._appointments.create(appointment)
        run.visits[visit_key] = created.id
        self._ledger(
            run, Landed("appointment", note.source_id, "appointments", created.id, note.digest)
        )
        run.report.counts["appointment"]["new"] += 1
        return created.id

    @staticmethod
    def _provenance(note: NoteRecord) -> dict[str, Any]:
        info = note.appointment
        return {
            "system": SOURCE_SYSTEM,
            "source_id": note.source_id,
            "kind": note.kind,
            "title": note.title,
            "signed_by": note.signed_by,
            "signed_at": note.signed_at.isoformat() if note.signed_at else None,
            "locked": note.locked,
            "created_at": note.created_at.isoformat() if note.created_at else None,
            "updated_at": note.updated_at.isoformat() if note.updated_at else None,
            "diagnoses": [{"code": d.code, "description": d.description} for d in note.diagnoses],
            "billing_code": info.billing_code if info else None,
            "billing_description": info.billing_description if info else None,
            "visit": (
                {
                    "kind": info.kind,
                    "starts_at": info.starts_at.isoformat(),
                    "ends_at": info.ends_at.isoformat(),
                    "duration_minutes": info.duration_minutes,
                }
                if info
                else None
            ),
        }

    @staticmethod
    def _finalized_at(note: NoteRecord) -> datetime:
        when = note.signed_at or note.updated_at or note.created_at
        if when is None and note.appointment is not None:
            when = note.appointment.ends_at
        return when or utc_now()

    def _land_session_note(self, note: NoteRecord, patient_id: str, author: str) -> str:
        """A finalized imported session carrying the note, verbatim.

        Sessions carry no appointment column; the completed visit created
        beside this session shares its date, and that date is the link.
        """
        info = note.appointment
        if info is None:
            raise ApplyRefusedError("A progress note without a visit cannot become a session.")
        patient = self._patients.get(patient_id, author)
        if patient is None:
            raise ApplyRefusedError("The mapped provider has no access to this client.")
        now = utc_now()
        starts = info.starts_at
        created = self._sessions.create(
            TherapySession(
                id=str(uuid.uuid4()),
                user_id=author,
                patient_id=patient_id,
                session_date=starts,
                session_number=self._sessions.get_session_number_for_patient(patient_id),
                status=SessionStatus.FINALIZED,
                transcript=Transcript(format=TranscriptFormat.TXT, content=note.body),
                source=SessionSource.IMPORTED.value,
                session_type=info.kind.lower(),
                duration_minutes=info.duration_minutes,
                created_at=now,
                processing_started_at=now,
                processing_completed_at=now,
            )
        )
        landed = self._notes.create_or_update_for_session(
            session_id=created.id,
            patient_id=patient_id,
            note_type="narrative",
            content={"note": {"body": note.body}, "__source": self._provenance(note)},
            user_id=author,
        )
        self._notes.finalize_note(landed.id, finalized_at=self._finalized_at(note), user_id=author)
        patient.session_count += 1
        if patient.last_session_date is None or starts > patient.last_session_date:
            patient.last_session_date = starts
        self._patients.update(patient)
        return landed.id

    def _land_standalone(
        self, note: NoteRecord, patient_id: str, author: str, note_type: str
    ) -> str:
        body = {"note": {"body": note.body}, "__source": self._provenance(note)}
        if note_type == "psychotherapy":
            # A restricted note may only start empty; its text goes in as the
            # author's own edit, which is what it is.
            landed = self._notes.create_standalone_note(
                patient_id=patient_id, note_type=note_type, content_edited=body, user_id=author
            )
        else:
            landed = self._notes.create_standalone_note(
                patient_id=patient_id, note_type=note_type, content=body, user_id=author
            )
        self._notes.finalize_note(landed.id, finalized_at=self._finalized_at(note), user_id=author)
        return landed.id

    # ------------------------------------------------------------------ questionnaires

    def _land_questionnaires(self, run: _Run) -> None:
        for q in run.archive.questionnaires:
            record = self._landable(run, "questionnaire", q.source_id)
            if record is None:
                continue
            patient_id = self._target_for(run, record)
            if patient_id is None:
                continue
            key = _key("questionnaire", q.source_id)
            if record["state"] == "changed":
                run.report.miss(
                    "questionnaire",
                    key,
                    "changed in the source; scores are not updated in this version",
                    bucket="conflict",
                )
                continue
            measure_id = self._land_questionnaire(
                q, patient_id, self._author_for(run, q.provider_name)
            )
            if measure_id is None:
                run.report.miss(
                    "questionnaire", key, f"instrument {q.instrument!r} is not known here"
                )
                continue
            self._ledger(
                run, Landed("questionnaire", q.source_id, "outcome_measures", measure_id, q.digest)
            )
            run.report.counts["questionnaire"]["new"] += 1

    def _land_questionnaire(
        self, q: QuestionnaireRecord, patient_id: str, author: str
    ) -> str | None:
        request = CreateOutcomeMeasureRequest(
            instrument=q.instrument,
            source=OutcomeMeasureSource.PATIENT_SELF_REPORT,
            administered_at=q.completed_at or utc_now(),
            item_scores={str(i.number): i.score for i in q.items} or None,
            total_score=q.total_score if not q.items else None,
        )
        try:
            created = self._measures.create(patient_id, request, author)
        except ValueError:
            return None
        return created.id

    # ------------------------------------------------------------------ messages

    def _land_threads(self, run: _Run) -> None:
        cards = {c.source_id: c for c in run.archive.contacts}
        for thread in run.archive.threads:
            record = self._landable(run, "thread", thread.source_id)
            if record is None:
                continue
            patient_id = self._target_for(run, record)
            if patient_id is None:
                continue
            key = _key("thread", thread.source_id)
            if record["state"] == "changed":
                run.report.miss(
                    "thread",
                    key,
                    "changed in the source; messages are not updated in this version",
                    bucket="conflict",
                )
                continue
            card_id = record["card_id"] or (run.decisions.get("assignments") or {}).get(key)
            card = cards.get(card_id) if card_id else None
            client_names = {card.display_name, card.folder_name} if card else set()
            thread_id = self._land_thread(thread, patient_id, client_names)
            if thread_id is None:
                run.report.miss("thread", key, "no messages to land", bucket="skipped")
                continue
            self._ledger(
                run,
                Landed(
                    "thread", thread.source_id, "patient_message_threads", thread_id, thread.digest
                ),
            )
            run.report.counts["thread"]["new"] += 1

    def _land_thread(
        self, thread: MessageThread, patient_id: str, client_names: set[str]
    ) -> str | None:
        messages = [m for m in thread.messages if m.sender != SYSTEM_SENDER and m.body]
        if not messages:
            return None
        first_at = messages[0].sent_at or utc_now()
        envelope = PatientMessageThread(
            id=str(uuid.uuid4()),
            patient_id=patient_id,
            subject=None,
            status="open",
            created_at=first_at,
            last_message_at=first_at,
        )
        built = [
            PatientMessage(
                id=str(uuid.uuid4()),
                thread_id=envelope.id,
                patient_id=patient_id,
                sender=SENDER_PATIENT if m.sender in client_names else SENDER_CLINICIAN,
                body=m.body,
                created_at=m.sent_at or first_at,
            )
            for m in messages
        ]
        self._messages.add_patient_thread(envelope, built[0])
        for message in built[1:]:
            if message.sender == SENDER_PATIENT:
                self._messages.add_patient_message(message)
            else:
                self._messages.add_reply(message, self._user_id)
        return envelope.id

    # ------------------------------------------------------------------ uploads

    def _land_uploads(self, run: _Run) -> None:
        for upload in run.archive.uploads:
            record = self._landable(run, "upload", upload.source_id)
            if record is None:
                continue
            patient_id = self._target_for(run, record)
            if patient_id is None:
                continue
            key = _key("upload", upload.source_id)
            mime = _UPLOAD_MIME.get(Path(upload.original_filename).suffix.lower())
            if mime is None:
                run.report.miss(
                    "upload", key, "file type is not one the chart stores", bucket="skipped"
                )
                continue
            document_id = self._land_upload(upload, patient_id, mime)
            if document_id is None:
                run.report.miss(
                    "upload", key, "no document storage is configured here", bucket="skipped"
                )
                continue
            self._ledger(
                run,
                Landed("upload", upload.source_id, "patient_documents", document_id, upload.digest),
            )
            run.report.counts["upload"]["new"] += 1

    def _land_upload(self, upload: Upload, patient_id: str, mime: str) -> str | None:
        data = (self._archive_dir / upload.path).read_bytes()
        try:
            init = self._documents.init_upload(
                patient_id=patient_id,
                filename=upload.original_filename,
                mime_type=mime,
                size_bytes=len(data),
                category=DocumentCategory.CHART,
                user_id=self._user_id,
            )
            self._documents.store_uploaded_bytes(init.document, data, mime)
            finalized = self._documents.finalize_upload(
                document_id=init.document.id, user_id=self._user_id
            )
        except (PatientDocumentError, ValueError, RuntimeError, NotImplementedError, OSError):
            # No bucket, an unsupported type, or a storage the stack cannot
            # write to: the upload is reported, the run carries on.
            return None
        return finalized.id

    # ------------------------------------------------------------------ practice

    def _land_practice(self, run: _Run, preview: dict[str, Any]) -> None:
        confirmed = run.decisions.get("practice") or {}
        proposals = preview.get("practice", {}).get("proposals", {})
        kind, minutes = proposals.get("visit_kind"), proposals.get("visit_minutes")
        if confirmed.get("appointment_type") and kind and minutes:
            repo = get_appointment_type_repository()
            name = f"{kind} session"
            already = any(
                t.name == name and t.duration_minutes == minutes
                for t in repo.list_by_user(self._user_id)
            )
            if not already:
                now = utc_now()
                created = repo.create(
                    AppointmentType(
                        id=str(uuid.uuid4()),
                        user_id=self._user_id,
                        name=name,
                        duration_minutes=int(minutes),
                        default_fee_cents=(
                            proposals.get("rate_cents") if confirmed.get("rate") else None
                        ),
                        created_at=now,
                        updated_at=now,
                    )
                )
                self._ledger(
                    run,
                    Landed(
                        "appointment_type",
                        f"{kind}:{minutes}",
                        "appointment_types",
                        created.id,
                        "practice",
                    ),
                )
                run.report.counts["practice"]["appointment_type"] += 1
        for item in ("provider_name", "rate", "appointment_type"):
            if confirmed.get(item):
                run.report.counts["practice"][f"confirmed:{item}"] += 1

    # ------------------------------------------------------------------ undo

    def undo(
        self, *, run_id: str, include_edited: bool, landed_until: datetime | None
    ) -> dict[str, Any]:
        """Take a run's landings back out, through the same deletion paths.

        A row the clinician edited after the run finished (``landed_until``)
        is kept unless ``include_edited`` is set; the report names what was
        kept. The run's end, not each row's landing time, is the line: a
        patient row is touched again by every session the same run lands.
        """
        removed: Counter[str] = Counter()
        kept: list[dict[str, str]] = []
        rows = records_for_run(self._session, run_id)
        for row in sorted(rows, key=lambda r: _UNDO_ORDER.get(r.record_type, len(_UNDO_ORDER))):
            if row.state == "undone":
                continue
            line = max(row.updated_at, landed_until) if landed_until else row.updated_at
            if not include_edited and edited_since(
                self._session, row.target_table, row.target_id, line
            ):
                kept.append(
                    {"key": _key(row.record_type, row.source_id), "reason": "edited since import"}
                )
                continue
            if self._remove(row.target_table, row.target_id):
                removed[row.record_type] += 1
            row.state = "undone"
            row.updated_at = utc_now()
        return {"removed": dict(removed), "kept": kept}

    def _remove(self, table: str, target_id: str) -> bool:
        removers = {
            "patients": lambda: self._patients.delete(target_id, self._user_id),
            "notes": lambda: self._notes_repo.delete(target_id, self._user_id),
            "appointments": lambda: self._appointments.delete(target_id, self._user_id),
            "outcome_measures": lambda: self._remove_measure(target_id),
            "patient_documents": lambda: get_patient_document_repository().soft_delete(
                target_id, self._user_id, utc_now()
            ),
            "patient_message_threads": lambda: self._remove_thread(target_id),
            "appointment_types": lambda: get_appointment_type_repository().delete(
                target_id, self._user_id
            ),
        }
        remover = removers.get(table)
        return bool(remover()) if remover else False

    def _remove_measure(self, measure_id: str) -> bool:
        self._measures.soft_delete(measure_id, self._user_id)
        return True

    def _remove_thread(self, thread_id: str) -> bool:
        # Messaging has no clinician delete path; an imported thread is removed
        # whole, messages first, under the same tenant session and RLS.
        self._session.execute(
            delete(PatientMessageRow).where(PatientMessageRow.thread_id == thread_id)
        )
        self._session.execute(
            delete(PatientMessageThreadRow).where(PatientMessageThreadRow.id == thread_id)
        )
        return True


#: Seconds after the line within which a row's own ``updated_at`` is the
#: landing itself, not a later edit.
_EDIT_GRACE_SECONDS = 5


def edited_since(session: Session, table: str, target_id: str, line: datetime) -> bool:
    """Whether a landed row was changed here after ``line``.

    Used twice: by undo, to keep what the clinician has since edited, and by
    the preview, to mark a record that changed in the source *and* here as a
    conflict rather than overwrite it.
    """
    model = _EDITABLE_TABLES.get(table)
    if model is None:
        return False
    updated: datetime | None = session.execute(
        select(model.updated_at).where(model.id == target_id)
    ).scalar_one_or_none()
    if updated is None:
        return False
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    if line.tzinfo is None:
        line = line.replace(tzinfo=UTC)
    # The landing's own write sits a moment before ``line``; only an edit
    # beyond this grace counts as the clinician's.
    return (updated - line).total_seconds() > _EDIT_GRACE_SECONDS


__all__ = [
    "PATIENT_ORIGIN",
    "ApplyRefusedError",
    "ApplyReport",
    "ArchiveApplier",
    "edited_since",
]
