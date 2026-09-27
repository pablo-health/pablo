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
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from ..db.models import ImportRecordRow, ImportRunRow
from ..utcnow import utc_now

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.orm import Session

#: How long an uploaded archive is kept after upload if never applied.
ARCHIVE_TTL = timedelta(hours=48)

#: Zip members are refused above this many bytes uncompressed, in total —
#: an archive is PDFs and small text, and a zip bomb is not.
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024


class ArchiveError(ValueError):
    """The uploaded archive could not be used as an export."""


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


def practice_archive_dir(base: Path, practice_key: str) -> Path:
    """Where this practice's uploaded archives live. The key is a hash of the
    tenant identifier, never the practice's name or schema."""
    digest = hashlib.sha256(practice_key.encode()).hexdigest()[:24]
    return base / digest


def store_archive(base: Path, practice_key: str, data: bytes) -> str:
    """Unpack an uploaded zip into a fresh directory and return its reference.

    The reference is the directory name, not a path: it is stored on the run
    row and resolved again through :func:`archive_path`, so a row never
    carries a filesystem location.
    """
    root = practice_archive_dir(base, practice_key)
    root.mkdir(parents=True, exist_ok=True)
    ref = uuid.uuid4().hex
    target = root / ref
    target.mkdir()
    try:
        _extract_zip(data, target)
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise
    return ref


def archive_path(base: Path, practice_key: str, ref: str) -> Path | None:
    if not ref or "/" in ref or ".." in ref:
        return None
    path = practice_archive_dir(base, practice_key) / ref
    return path if path.is_dir() else None


def delete_archive(base: Path, practice_key: str, ref: str | None) -> bool:
    if not ref:
        return False
    path = archive_path(base, practice_key, ref)
    if path is None:
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def sweep_expired_archives(session: Session, base: Path, practice_key: str, now: datetime) -> int:
    """Delete every archive whose run has passed ``archive_expires_at``."""
    stmt = select(ImportRunRow).where(
        ImportRunRow.archive_ref.is_not(None), ImportRunRow.archive_expires_at < now
    )
    swept = 0
    for run in session.execute(stmt).scalars():
        delete_archive(base, practice_key, run.archive_ref)
        run.archive_ref = None
        run.archive_expires_at = None
        swept += 1
    return swept


def _extract_zip(data: bytes, target: Path) -> None:
    try:
        zf = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ArchiveError("That file is not a zip archive.") from exc
    with zf:
        total = 0
        members = [m for m in zf.infolist() if not m.is_dir()]
        if not members:
            raise ArchiveError("The zip archive is empty.")
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
        tops = {Path(m.filename).parts[0] for m in members if "__MACOSX" not in m.filename}
        nested = all(len(Path(m.filename).parts) > 1 for m in members)
        strip = next(iter(tops)) if len(tops) == 1 and nested else None
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
    "Landed",
    "LedgerEntry",
    "archive_path",
    "delete_archive",
    "get_run",
    "ledger_snapshot",
    "list_runs",
    "new_run",
    "record_landed",
    "records_for_run",
    "store_archive",
    "sweep_expired_archives",
]
