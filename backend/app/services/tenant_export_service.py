# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tenant-wide PHI export — streams a tar.gz of every clinical row in the tenant.

Drives :func:`backend.app.routes.admin.tenant_export` (POST
/api/admin/tenant-export). Used by:

* Practice admins downloading a self-service archive.
* The SaaS overlay's offboarding flow, which surfaces this endpoint to
  CEs before schema teardown.

Design notes:

* **Streaming.** The archive is built incrementally with
  ``tarfile.open(fileobj=stream, mode='w|gz')`` (the streaming mode
  that never seeks). A custom :class:`_PipeWriter` collects ``write``
  calls into a queue; the route's StreamingResponse drains the queue.
  The full archive is never materialized in memory.
* **Schema-scoped, not user-scoped.** All queries hit the request's
  schema directly (no per-user filter). The tenant is identified by
  the session's ``search_path``; cross-tenant isolation is the
  middleware's job.
* **PHI-free audit.** The completion audit log records counts and
  ``size_bytes`` only — never row contents.
* **Audio is out of scope (v1).** ``include_audio`` requests are
  accepted for forward compatibility but ignored; only structured
  rows ship in the archive.
"""

from __future__ import annotations

import csv
import io
import json
import tarfile
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import select

from ..db.models import (
    AuditLogRow,
    NoteRow,
    PatientRow,
    TherapySessionRow,
)
from ..utcnow import utc_now_iso

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    from sqlalchemy.orm import Session


ExportFormat = Literal["json", "csv"]


# Row-type tables included in the archive. Order is stable so the
# audit log's ``counts`` payload is deterministic and so consumers
# can rely on a predictable extraction order.
_ROW_TYPES: tuple[str, ...] = (
    "patients",
    "therapy_sessions",
    "notes",
    "audit_logs",
)


@dataclass(frozen=True)
class TenantExportSummary:
    """Result emitted after the archive stream completes.

    ``size_bytes`` is the on-the-wire archive size (post-gzip).
    ``counts`` maps each row type to the number of rows shipped.
    Both fields are PHI-free and safe for the audit log payload.
    """

    size_bytes: int
    counts: dict[str, int]


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Flatten an ORM row into a JSON-serializable dict.

    Walks the SQLAlchemy column descriptors instead of ``__dict__`` so
    we never accidentally include relationship caches or internal
    SQLAlchemy state. ``datetime`` values are emitted as ISO 8601
    strings so plain ``json.dumps`` works without a custom encoder.
    """
    out: dict[str, Any] = {}
    for col in row.__table__.columns:
        value = getattr(row, col.name)
        if isinstance(value, datetime):
            out[col.name] = value.isoformat().replace("+00:00", "Z")
        else:
            out[col.name] = value
    return out


def _iter_rows(db: Session, model: type[Any]) -> Iterator[Any]:
    """Yield every row for ``model`` in the current tenant schema."""
    yield from db.execute(select(model)).scalars()


def _row_iter_for(table: str, db: Session) -> Iterator[Any]:
    if table == "patients":
        return _iter_rows(db, PatientRow)
    if table == "therapy_sessions":
        return _iter_rows(db, TherapySessionRow)
    if table == "notes":
        return _iter_rows(db, NoteRow)
    if table == "audit_logs":
        return _iter_rows(db, AuditLogRow)
    msg = f"Unknown tenant export table: {table!r}"
    raise ValueError(msg)


def _serialize_json(rows: Iterable[Any]) -> tuple[bytes, int]:
    """Serialize ``rows`` to a JSON array; return (bytes, count)."""
    items = [_row_to_dict(r) for r in rows]
    payload = json.dumps(items, default=str, separators=(",", ":")).encode("utf-8")
    return payload, len(items)


def _serialize_csv(rows: Iterable[Any]) -> tuple[bytes, int]:
    """Serialize ``rows`` to CSV; return (bytes, count).

    Empty tables still emit a zero-byte payload (no header) so the
    archive layout is uniform. JSONB columns are dumped as compact
    JSON strings since CSV has no native nested-value form.
    """
    materialized = [_row_to_dict(r) for r in rows]
    if not materialized:
        return b"", 0
    buf = io.StringIO()
    fieldnames = list(materialized[0].keys())
    writer = csv.DictWriter(buf, fieldnames=fieldnames)
    writer.writeheader()
    for row in materialized:
        writer.writerow(
            {
                k: (
                    json.dumps(v, default=str, separators=(",", ":"))
                    if isinstance(v, (dict, list))
                    else v
                )
                for k, v in row.items()
            }
        )
    return buf.getvalue().encode("utf-8"), len(materialized)


class _PipeWriter:
    """File-like object that buffers ``write`` calls for a generator.

    ``tarfile.open(fileobj=..., mode='w|gz')`` treats its ``fileobj``
    as a sink: it calls ``write(bytes)`` repeatedly and never seeks.
    We collect those writes into a list-buffer so the surrounding
    generator can yield them as the archive is being built.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []
        self._total: int = 0

    def write(self, data: bytes) -> int:
        if data:
            self._chunks.append(data)
            self._total += len(data)
        return len(data)

    def drain(self) -> bytes:
        if not self._chunks:
            return b""
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out

    def flush(self) -> None:
        """No-op; tarfile calls flush() between members."""

    @property
    def total_bytes(self) -> int:
        return self._total


def stream_tenant_archive(
    db: Session,
    *,
    export_format: ExportFormat = "json",
    on_complete: Callable[[TenantExportSummary], None] | None = None,
) -> Iterator[bytes]:
    """Yield successive byte chunks of a tar.gz archive of the tenant.

    The archive contains one file per row-type in ``_ROW_TYPES``. The
    file extension matches ``export_format`` (``.json`` or ``.csv``).
    A small ``manifest.json`` is added last with counts and the export
    timestamp.

    Parameters
    ----------
    db:
        Request-scoped SQLAlchemy session whose ``search_path`` is
        already set to the tenant's schema. The caller is responsible
        for tenant isolation; this function does not check.
    export_format:
        ``"json"`` (default) or ``"csv"``.
    on_complete:
        Optional callback invoked once after the final byte is
        produced, with the :class:`TenantExportSummary`. Routes use
        this to emit the ``TENANT_EXPORTED`` audit log without buffering
        the archive.
    """
    pipe = _PipeWriter()
    counts: dict[str, int] = {}
    exported_at = utc_now_iso()
    serializer = _serialize_csv if export_format == "csv" else _serialize_json
    extension = "csv" if export_format == "csv" else "json"

    # Stream tar.gz: ``mode='w|gz'`` is the non-seeking streaming form.
    # The ``with`` block guarantees the gzip footer is written even if
    # a serializer raises mid-stream.
    with tarfile.open(fileobj=pipe, mode="w|gz") as tar:
        for table in _ROW_TYPES:
            payload, count = serializer(_row_iter_for(table, db))
            counts[table] = count
            info = tarfile.TarInfo(name=f"{table}.{extension}")
            info.size = len(payload)
            info.mtime = 0  # deterministic — no PHI in mtimes either
            tar.addfile(info, io.BytesIO(payload))
            chunk = pipe.drain()
            if chunk:
                yield chunk

        manifest = {
            "exported_at": exported_at,
            "format": export_format,
            "include_audio": False,
            "counts": counts,
        }
        manifest_bytes = json.dumps(manifest, separators=(",", ":")).encode("utf-8")
        info = tarfile.TarInfo(name="manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(manifest_bytes))
        chunk = pipe.drain()
        if chunk:
            yield chunk

    chunk = pipe.drain()
    if chunk:
        yield chunk

    if on_complete is not None:
        on_complete(TenantExportSummary(size_bytes=pipe.total_bytes, counts=counts))
