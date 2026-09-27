# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The practice export: every client's archive and the practice-wide files, as one ZIP.

Streamed, not buffered: a practice is many charts, and each patient's
archive is built and written before the next is read, so the response
starts at once and memory holds one archive at a time. Each archive is the
one the chart's own Export produces, byte for byte, at
``patients/<patient_id>/<its filename>``. Beside them, ``clients.csv`` and
``appointments.csv`` for the whole practice, ``audit_log.csv`` with the
rows the caller can read, and ``manifest.json`` with every file's size and
SHA-256.

What is in it is what the caller can see: the patients are the ones they
hold a grant on, and each archive is built as them. A chart the caller
cannot open is not in the copy.
"""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..models.export import ExportManifest, ExportOptions, ManifestFile
from .export_archive import json_bytes
from .export_csv import appointments_csv, clients_csv
from .tenant_export_service import PipeWriter

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from datetime import datetime

    from ..models import Patient
    from ..models.export import ManifestFileKind, PatientExportDocument

#: Given a patient's id, that patient's archive as the chart export builds it.
PatientArchiveBuilder = Callable[[str], dict[str, Any]]


@dataclass(frozen=True)
class PracticeExportSummary:
    """What the stream wrote, for the audit row. Set only once the last byte is out."""

    size_bytes: int
    patients: int
    files: int
    options: ExportOptions


@dataclass
class PracticeExportState:
    """Holder the route reads from its background task after the response is sent.

    ``exported`` is each patient and what left in their archive, in order,
    so the route can record every one the way the chart's export does.
    ``summary`` stays ``None`` when the stream did not run to completion.
    """

    exported: list[tuple[Patient, dict[str, Any]]] = field(default_factory=list)
    summary: PracticeExportSummary | None = None


def patient_archive_path(patient_id: str, filename: str) -> str:
    return f"patients/{patient_id}/{filename}"


def stream_practice_archive(
    *,
    patients: Iterable[Patient],
    build: PatientArchiveBuilder,
    audit_log: Callable[[], bytes],
    options: ExportOptions,
    exported_at: datetime,
    state: PracticeExportState | None = None,
) -> Iterator[bytes]:
    """Yield the ZIP in chunks: one patient's archive at a time, then the practice files.

    ``build`` is the chart export for one patient (``zip`` format, the
    caller's options already applied); it returns the archive bytes, its
    filename and the export document, which is what the practice-wide
    CSVs are written from. ``audit_log`` is read last, so it can include
    the export itself when the caller records it before streaming.
    """
    pipe = PipeWriter()
    stamp = exported_at.timetuple()[:6]
    described: list[ManifestFile] = []
    documents: list[PatientExportDocument] = []
    count = 0

    def entry(path: str, data: bytes, kind: ManifestFileKind) -> bytes:
        archive.writestr(zipfile.ZipInfo(path, date_time=stamp), data, zipfile.ZIP_DEFLATED)
        described.append(
            ManifestFile(
                path=path, bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), kind=kind
            )
        )
        return pipe.drain()

    # ``PipeWriter`` has no ``tell``; zipfile notices and writes data
    # descriptors instead of seeking back to each header.
    with zipfile.ZipFile(pipe, "w", compression=zipfile.ZIP_DEFLATED) as archive:  # type: ignore[arg-type]
        for patient in patients:
            exported = build(patient.id)
            documents.append(exported["document"])
            if state is not None:
                state.exported.append((patient, exported))
            count += 1
            yield entry(
                patient_archive_path(patient.id, exported["filename"]),
                exported["content"],
                "archive",
            )

        yield entry("clients.csv", clients_csv(documents).encode(), "csv")
        yield entry("appointments.csv", appointments_csv(documents).encode(), "csv")
        yield entry("audit_log.csv", audit_log(), "csv")

        manifest = ExportManifest(exported_at=exported_at, options=options, files=described)
        archive.writestr(
            zipfile.ZipInfo("manifest.json", date_time=stamp),
            json_bytes(manifest.model_dump(mode="json")),
            zipfile.ZIP_DEFLATED,
        )
        yield pipe.drain()

    yield pipe.drain()

    if state is not None:
        state.summary = PracticeExportSummary(
            size_bytes=pipe.total_bytes,
            patients=count,
            files=len(described) + 1,
            options=options,
        )
