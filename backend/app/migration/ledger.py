# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The import ledger and the uploaded archive.

``import_runs`` is one row per run; ``import_records`` is one row per landed
source record keyed by the source system's own id. Together they are what
makes a second run of the same archive exact (seen ids are skipped or
updated, never duplicated) and what lets a run be undone (every row it
created is named).

The uploaded archive itself lives on disk under a per-practice directory
for as long as it is needed: until apply, and at most until
``archive_expires_at`` (48 hours), after which any preview or apply sweeps
it away. Nothing here is a scheduler; the sweep runs whenever the practice
next touches the import screen, which is the only time the archive could
matter again.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sqlalchemy import select

from ..db.models import ImportRecordRow, ImportRunRow
from ..utcnow import utc_now
from .readers.simplepractice_calendar import CALENDAR_DIR

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

#: How long an uploaded archive is kept after upload if never applied.
ARCHIVE_TTL = timedelta(hours=48)

#: Zip members are refused above this many bytes uncompressed, in total —
#: an archive is PDFs and small text, and a zip bomb is not.
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class ArchiveError(ValueError):
    """The uploaded archive could not be used as an export.

    ``code`` tells the screen what to do about it: ``ARCHIVE_PASSWORD_REQUIRED``
    and ``ARCHIVE_PASSWORD_WRONG`` point at the password field; anything else
    is ``INVALID_ARCHIVE``, a different file.
    """

    def __init__(self, message: str, *, code: str = "INVALID_ARCHIVE") -> None:
        super().__init__(message)
        self.code = code


class ArchiveRemover(Protocol):
    """What the sweep needs of an archive store: removal by practice and reference."""

    def delete(self, practice_key: str, ref: str | None) -> bool: ...


@dataclass(frozen=True)
class LedgerEntry:
    record_type: str
    source_id: str
    target_table: str
    target_id: str
    source_digest: str
    state: str
    run_id: str


def ledger_snapshot(session: Session, source_system: str) -> dict[tuple[str, str], LedgerEntry]:
    """Every live ledger row for ``source_system``, keyed by (type, source id)."""
    rows = session.execute(
        select(ImportRecordRow).where(ImportRecordRow.source_system == source_system)
    ).scalars()
    return {
        (r.record_type, r.source_id): LedgerEntry(
            record_type=r.record_type,
            source_id=r.source_id,
            target_table=r.target_table,
            target_id=r.target_id,
            source_digest=r.source_digest,
            state=r.state,
            run_id=r.run_id,
        )
        for r in rows
    }


@dataclass(frozen=True)
class Landed:
    """One source record that just landed on (or updated) a target row."""

    record_type: str
    source_id: str
    target_table: str
    target_id: str
    source_digest: str
    previous_payload: dict[str, Any] | None = None


def record_landed(session: Session, source_system: str, run_id: str, landed: Landed) -> None:
    """Upsert the ledger row for one landed or updated source record."""
    now = utc_now()
    row = session.get(ImportRecordRow, (source_system, landed.record_type, landed.source_id))
    if row is None:
        row = ImportRecordRow(
            source_system=source_system,
            record_type=landed.record_type,
            source_id=landed.source_id,
            target_table=landed.target_table,
            target_id=landed.target_id,
            source_digest=landed.source_digest,
            run_id=run_id,
            state="landed",
            previous_payload=landed.previous_payload,
            imported_at=now,
            updated_at=now,
        )
        session.add(row)
        return
    row.target_table = landed.target_table
    row.target_id = landed.target_id
    row.source_digest = landed.source_digest
    row.run_id = run_id
    row.state = "updated" if row.state != "undone" else "landed"
    row.previous_payload = landed.previous_payload
    row.updated_at = now


def records_for_run(session: Session, run_id: str) -> list[ImportRecordRow]:
    return list(
        session.execute(select(ImportRecordRow).where(ImportRecordRow.run_id == run_id)).scalars()
    )


#: How a standing ledger row reads on a screen. ``undone`` is not one: a row
#: that was undone is filtered out before anything shows it.
RecordState = Literal["landed", "updated", "merged"]


def record_state(row: ImportRecordRow) -> RecordState:
    """How a ledger row reads on a screen: ``landed``, ``updated`` or ``merged``.

    A merge is marked in ``previous_payload`` rather than ``state`` (the state
    column is what undo keys on, and a merged patient is never removed), so
    the two are folded together here for anything that shows the row.
    """
    if row.state == "undone":
        raise ValueError("an undone record is not shown")
    if (row.previous_payload or {}).get("merged"):
        return "merged"
    return "updated" if row.state == "updated" else "landed"


def client_records_for_run(session: Session, run_id: str) -> list[ImportRecordRow]:
    """The run's contact records that still stand: each names a patient it landed on.

    An undone record is left out; whether its patient still exists is for the
    caller's patient lookup to say, under the clinician's own grant.
    """
    stmt = select(ImportRecordRow).where(
        ImportRecordRow.run_id == run_id,
        ImportRecordRow.record_type == "contact",
        ImportRecordRow.target_table == "patients",
        ImportRecordRow.state != "undone",
    )
    return list(session.execute(stmt).scalars())


@dataclass(frozen=True)
class ImportOrigin:
    """The import that put (or merged) a record here, as the chart shows it."""

    run_id: str
    source_system: str
    started_at: datetime
    finished_at: datetime | None
    state: RecordState


def origin_of(session: Session, target_table: str, target_id: str) -> ImportOrigin | None:
    """The latest standing import that landed on ``target_id``, or ``None``.

    ``None`` for a row added by hand, and for one whose import was undone:
    an undone run no longer accounts for anything in the chart.
    """
    stmt = (
        select(ImportRecordRow, ImportRunRow)
        .join(ImportRunRow, ImportRunRow.id == ImportRecordRow.run_id)
        .where(
            ImportRecordRow.target_table == target_table,
            ImportRecordRow.target_id == target_id,
            ImportRecordRow.state != "undone",
        )
        .order_by(ImportRecordRow.imported_at.desc())
        .limit(1)
    )
    found = session.execute(stmt).first()
    if found is None:
        return None
    record, run = found._tuple()
    return ImportOrigin(
        run_id=run.id,
        source_system=run.source_system,
        started_at=run.started_at,
        finished_at=run.finished_at,
        state=record_state(record),
    )


def new_run(
    session: Session, *, source_system: str, scope: str, started_by: str, archive_ref: str | None
) -> ImportRunRow:
    now = utc_now()
    run = ImportRunRow(
        id=str(uuid.uuid4()),
        source_system=source_system,
        scope=scope,
        state="queued",
        started_by=started_by,
        started_at=now,
        archive_ref=archive_ref,
        archive_expires_at=(now + ARCHIVE_TTL) if archive_ref else None,
    )
    session.add(run)
    session.flush()
    return run


def get_run(session: Session, run_id: str) -> ImportRunRow | None:
    return session.get(ImportRunRow, run_id)


def list_runs(session: Session, *, limit: int = 50) -> list[ImportRunRow]:
    stmt = select(ImportRunRow).order_by(ImportRunRow.started_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars())


# --------------------------------------------------------------------------- archives


def practice_archive_key(practice_key: str) -> str:
    """The name a practice's archives are filed under, on disk and in the object
    store: a hash of the tenant identifier, never the practice's name or schema."""
    return hashlib.sha256(practice_key.encode()).hexdigest()[:24]


def practice_archive_dir(base: Path, practice_key: str) -> Path:
    """Where this practice's uploaded archives live on this instance."""
    return base / practice_archive_key(practice_key)


def store_archive(
    base: Path, practice_key: str, data: Path | bytes, password: str | None = None
) -> str:
    """Unpack an uploaded zip into a fresh directory and return its reference.

    The local half of :class:`app.migration.archive_store.ArchiveStore`, which
    is what the routes and jobs use; kept for a deployment that reads the
    unpacked directory directly.

    The reference is the directory name, not a path: it is stored on the run
    row and resolved again through :func:`archive_path`, so a row never
    carries a filesystem location.

    ``password`` opens an export the source system protected (SimplePractice
    protects every one, with the traditional zip cipher). It is used here
    once and kept nowhere: what lands on disk is the plain tree.
    """
    root = practice_archive_dir(base, practice_key)
    root.mkdir(parents=True, exist_ok=True)
    ref = uuid.uuid4().hex
    target = root / ref
    target.mkdir()
    try:
        extract_zip(data, target, password)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return ref


def archive_path(base: Path, practice_key: str, ref: str) -> Path | None:
    if not ref or "/" in ref or ".." in ref:
        return None
    path = practice_archive_dir(base, practice_key) / ref
    return path if path.is_dir() else None


def archive_file(root: Path, relative: str) -> Path | None:
    """The file ``relative`` names inside an unpacked archive, or ``None``.

    ``relative`` arrives from a request, so it is joined and fully resolved
    (symlinks included) before anything touches the disk, and anything that
    lands outside ``root`` is refused: a ``..`` segment, an absolute path, or
    a link that points elsewhere.
    """
    base = os.path.realpath(root)
    full = os.path.realpath(Path(base) / relative)
    if not full.startswith(base + os.sep):
        return None
    target = Path(full)
    return target if target.is_file() else None


def delete_archive(base: Path, practice_key: str, ref: str | None) -> bool:
    if not ref:
        return False
    path = archive_path(base, practice_key, ref)
    if path is None:
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def sweep_expired_archives(
    session: Session, store: ArchiveRemover, practice_key: str, now: datetime
) -> int:
    """Delete every archive whose run has passed ``archive_expires_at``.

    ``store`` is anything with ``delete(practice_key, ref)``: the configured
    :class:`~app.migration.archive_store.ArchiveStore`, which clears the
    object-store copy as well as this instance's.
    """
    stmt = select(ImportRunRow).where(
        ImportRunRow.archive_ref.is_not(None), ImportRunRow.archive_expires_at < now
    )
    swept = 0
    for run in session.execute(stmt).scalars():
        store.delete(practice_key, run.archive_ref)
        run.archive_ref = None
        run.archive_expires_at = None
        swept += 1
    return swept


#: WinZip AES marks a member with this compression method; the standard library
#: reads only the traditional cipher, which is the one SimplePractice uses.
_AES_COMPRESSION = 99


def _unlock(zf: zipfile.ZipFile, members: list[zipfile.ZipInfo], password: str | None) -> None:
    """Set the archive's password when its members need one, and prove it opens them.

    A protected export with no password, or the wrong one, is said plainly so
    the screen can point at the password field rather than call the file bad.
    """
    encrypted = [m for m in members if m.flag_bits & 0x1]
    if any(m.compress_type == _AES_COMPRESSION for m in encrypted):
        raise ArchiveError(
            "This zip is protected in a way Pablo can't open. Unzip it, then zip the "
            "folder again without a password.",
        )
    if encrypted and not password:
        raise ArchiveError(
            "This export is protected with a password. Enter the one shown with the export.",
            code="ARCHIVE_PASSWORD_REQUIRED",
        )
    if not password:
        return
    zf.setpassword(password.encode("utf-8"))
    try:
        with zf.open(encrypted[0] if encrypted else members[0]) as probe:
            probe.read(1)
    except RuntimeError as exc:  # the standard library's "Bad password" signal
        raise ArchiveError(
            "That password doesn't open this export. Check it and try again.",
            code="ARCHIVE_PASSWORD_WRONG",
        ) from exc


def extract_zip(source: Path | bytes, target: Path, password: str | None = None) -> None:
    """Unpack ``source``, a zip on disk or in memory, into ``target``.

    An upload is spooled to disk by the route and read from there, so a large
    export never has to fit in memory twice.
    """
    try:
        zf = zipfile.ZipFile(BytesIO(source) if isinstance(source, bytes) else source)
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That file is not a zip archive.") from exc
    with zf:
        total = 0
        members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            raise ArchiveError("The zip archive is empty.")
        _unlock(zf, members, password)
        for member in members:
            name = member.filename
            if name.startswith("/") or ".." in Path(name).parts or "__MACOSX" in name:
                if "__MACOSX" in name:
                    continue
                raise ArchiveError("The zip archive contains an unsafe path.")
            total += member.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise ArchiveError("The zip archive is too large.")
        # An export zipped from its folder usually has one top-level directory
        # ("Export - Complete - ..."); unwrap it so the reader sees the tree.
        # A zip of the saved calendar folder alone is already the tree, so
        # that one top-level directory is kept.
        tops = {Path(m.filename).parts[0] for m in members if "__MACOSX" not in m.filename}
        nested = all(len(Path(m.filename).parts) > 1 for m in members)
        strip = next(iter(tops)) if len(tops) == 1 and nested else None
        if strip == CALENDAR_DIR:
            strip = None
        for member in members:
            if "__MACOSX" in member.filename:
                continue
            rel = Path(member.filename)
            if strip is not None:
                rel = Path(*rel.parts[1:])
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out)


__all__ = [
    "ARCHIVE_TTL",
    "ArchiveError",
    "ImportOrigin",
    "Landed",
    "LedgerEntry",
    "RecordState",
    "archive_path",
    "client_records_for_run",
    "delete_archive",
    "get_run",
    "ledger_snapshot",
    "list_runs",
    "new_run",
    "origin_of",
    "record_landed",
    "record_state",
    "records_for_run",
    "store_archive",
    "sweep_expired_archives",
]
