# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Storage for the practice's own empty paperwork.

The same two-phase signed-URL upload every other file in the system uses —
the browser sends to storage directly, and the row is not offerable until
finalize has checked what landed — with one difference that shapes the
whole module: there is no patient anywhere in it.

That is why this is its own small service rather than a branch inside
:mod:`app.services.patient_documents_service`. Every method there takes a
principal and answers a question about whose chart a file is on. None of
those questions has an answer here, and a service that took a patient id it
never used would invite somebody to believe it meant something.

What IS shared is the file rules — the accepted types and the size cap come
from the same settings, so a practice cannot upload a blank form its own
patients' browsers would be refused.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ..intake.items import UPLOAD_MIME_TYPES
from ..utcnow import utc_now

if TYPE_CHECKING:
    from ..repositories.intake_blank_form import IntakeBlankFormRepository
    from ..settings import Settings
    from .file_storage import FileStorageProvider, UploadTarget

logger = logging.getLogger(__name__)


class IntakeBlankFormError(Exception):
    """Base class for blank-form service errors."""


class UnsupportedBlankFormTypeError(IntakeBlankFormError):
    def __init__(self, mime_type: str) -> None:
        super().__init__(f"Unsupported mime type: {mime_type!r}")
        self.mime_type = mime_type


class BlankFormTooLargeError(IntakeBlankFormError):
    def __init__(self, size_bytes: int, max_bytes: int) -> None:
        super().__init__(f"File too large: {size_bytes} bytes (max {max_bytes} bytes)")
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes


class BlankFormNotConfiguredError(IntakeBlankFormError):
    """No documents bucket on this deployment.

    Answered as a configuration problem rather than a bad request, the same
    way the patient document surface answers it: a self-hoster who has not
    provisioned a bucket should read a sentence about configuration, not an
    opaque failure from a storage SDK.
    """


class UploadNotCompleteError(IntakeBlankFormError):
    """Finalize was called but the storage object isn't there yet."""


@dataclass(frozen=True)
class InitBlankFormResult:
    form_id: str
    upload: UploadTarget
    max_bytes: int


class IntakeBlankFormService:
    """Upload, list, serve and retire the practice's blank forms."""

    def __init__(
        self,
        *,
        repo: IntakeBlankFormRepository,
        settings: Settings,
        storage: FileStorageProvider | None = None,
        tenant_id: str | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._storage_provider = storage
        self._tenant_id = tenant_id

    # --- storage plumbing ---

    def _storage(self) -> FileStorageProvider:
        if self._storage_provider is None:
            from .file_storage import file_storage_from_settings

            self._storage_provider = file_storage_from_settings(self._settings)
        return self._storage_provider

    def _bucket(self) -> str:
        bucket = self._settings.patient_documents_gcs_bucket
        if not bucket:
            raise BlankFormNotConfiguredError("patient_documents_gcs_bucket is not configured")
        return bucket

    def _object_name(self, form_id: str) -> str:
        """Where the file lives: ``<tenant>/intake_blank_form/<uuid>``.

        The same layout the patient document surface uses, with the
        category segment set to this surface's own name — so a bucket-policy
        review can still confirm tenant isolation at the storage layer, and
        a forensic read of the bucket can tell a practice's stationery from
        a patient's records without a database join.
        """
        prefix = self._tenant_id or "default"
        return f"{prefix}/intake_blank_form/{form_id}"

    # --- init / finalize ---

    def init_upload(
        self,
        *,
        title: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        uploaded_by: str,
    ) -> InitBlankFormResult:
        """Mint a signed upload target and insert the placeholder row.

        ``size_bytes`` is what the client claims, and refusing an obviously
        oversized one here is what stops a storage path being reserved for
        a file that was never going to be accepted. The real check is
        against the stored object at finalize, as everywhere else.
        """
        if mime_type not in UPLOAD_MIME_TYPES:
            raise UnsupportedBlankFormTypeError(mime_type)
        max_bytes = self._settings.patient_documents_max_bytes
        if size_bytes > max_bytes:
            raise BlankFormTooLargeError(size_bytes, max_bytes)
        form_id = str(uuid.uuid4())
        object_name = self._object_name(form_id)
        upload = self._storage().make_upload_target(
            bucket=self._bucket(),
            object_name=object_name,
            content_type=mime_type,
            max_bytes=max_bytes,
            ttl_seconds=self._settings.patient_documents_upload_url_ttl_seconds,
        )
        self._repo.add(
            {
                "id": form_id,
                "title": title.strip(),
                "filename": filename,
                "mime_type": mime_type,
                "gcs_path": object_name,
                "size_bytes": 0,
                "uploaded_by": uploaded_by,
                "created_at": utc_now(),
            }
        )
        return InitBlankFormResult(form_id=form_id, upload=upload, max_bytes=max_bytes)

    def finalize_upload(self, form_id: str) -> dict[str, object] | None:
        """Check the stored object and stamp the row finished.

        Idempotent: a row that is already finished comes back unchanged, so
        a retry after a dropped connection is not a second upload.
        """
        row = self._repo.get(form_id)
        if row is None:
            return None
        if row.get("finalized_at") is not None:
            return row
        metadata = self._storage().fetch_metadata(
            bucket=self._bucket(), object_name=str(row["gcs_path"])
        )
        if metadata is None:
            raise UploadNotCompleteError("storage object not found")
        size_bytes, _content_type = metadata
        return self._repo.mark_finalized(form_id, size_bytes=size_bytes, finalized_at=utc_now())

    # --- reads ---

    def list_all(self) -> list[dict[str, object]]:
        return self._repo.list_all()

    def signed_download_url(
        self, form_id: str, *, disposition: Literal["attachment", "inline"] = "attachment"
    ) -> str | None:
        """A short-lived signed URL, or ``None`` when there is no such form.

        The caller turns ``None`` into the one refusal every miss gets, so
        a deleted form, an unfinished upload and an id that names something
        else are indistinguishable.
        """
        row = self._repo.get_finalized(form_id)
        if row is None:
            return None
        return self._storage().make_download_url(
            bucket=self._bucket(),
            object_name=str(row["gcs_path"]),
            ttl_seconds=self._settings.patient_documents_download_url_ttl_seconds,
            response_disposition=f'{disposition}; filename="{_sanitize(str(row["filename"]))}"',
        )

    # --- writes ---

    def soft_delete(self, form_id: str) -> bool:
        return self._repo.soft_delete(form_id, utc_now())


def _sanitize(name: str) -> str:
    """Strip anything that could break out of ``Content-Disposition``."""
    return name.replace("\r", "").replace("\n", "").replace('"', "").strip() or "form"


__all__ = [
    "BlankFormNotConfiguredError",
    "BlankFormTooLargeError",
    "InitBlankFormResult",
    "IntakeBlankFormError",
    "IntakeBlankFormService",
    "UnsupportedBlankFormTypeError",
    "UploadNotCompleteError",
]
