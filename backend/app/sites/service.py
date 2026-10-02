# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Saving, publishing and rolling back a practice's website.

Files are kept in the configured bucket (``practice_site_bucket``) under
``sites/<practice_id>/``:

* ``draft/<draft_id>/<path>`` — the draft. Each upload goes to a new folder and
  the row then points at it, so a draft is never seen half-replaced;
* ``v<N>/<path>`` — published version *N*. Publishing copies the draft there
  in full and only then makes *N* live, in the same transaction that records
  the version; a publish that fails part-way leaves a folder nothing points
  at and a version number that is never used again.

The newest :data:`RETAINED_VERSIONS` versions are kept, plus the live one if
it is older; :func:`tidy_practice_site` removes the rest, and any draft folder
that is no longer the draft, after the change that left them has committed.

A website's ``theme.json`` (:mod:`app.sites.theme`) is read when the draft is
saved, so the practice sees what it gives the portal, and again as the draft is
published; the theme is kept with the version, so a roll back brings back the
theme that version had.

Every change holds the practice's row lock (:meth:`PracticeSiteStore.lock`)
from its first write to its commit, tidying included, so two changes to one
practice's website never interleave.

Two ways in, the same rules for both. Settings > Website uploads a zip
(:func:`app.sites.files.read_zip`), and something a deployment runs itself can
hand over files it made in memory: :meth:`PracticeSiteService.save_draft_files`
for a draft the practice previews first, or
:meth:`PracticeSiteService.publish_files` to publish at once. Both are checked
by :func:`app.sites.files.check_files` and audited like an upload.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from fastapi import Depends

from ..db.platform_models import PracticeSiteVersionRow
from ..models.audit import AuditAction, ResourceType
from ..services.audit_service import AuditService, get_audit_service
from ..settings import get_settings
from ..utcnow import utc_now
from .files import SiteFiles, check_files, content_type_for
from .storage import site_storage
from .store import PracticeSiteStore
from .theme import THEME_FILE, PracticeTheme, ThemeReport, read_theme, storable_theme, stored_theme

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from datetime import datetime

    from fastapi import Request
    from sqlalchemy.orm import Session

    from ..db.platform_models import PracticeSiteRow
    from ..models import User
    from ..services.file_storage import FileStorageProvider

#: How many of the newest published versions are kept to roll back to.
RETAINED_VERSIONS = 10
#: How long a draft's preview address works.
PREVIEW_TTL = timedelta(hours=1)
_OCTET_STREAM = "application/octet-stream"


class SiteNotConfiguredError(Exception):
    """This deployment names no bucket for websites."""


class NoDraftError(Exception):
    """There is no draft to publish or preview."""


class UnknownVersionError(Exception):
    """No retained version has that number."""


def site_prefix(practice_id: str) -> str:
    return f"sites/{practice_id}/"


def version_prefix(practice_id: str, version: int) -> str:
    return f"sites/{practice_id}/v{version}/"


def draft_prefix(practice_id: str, draft_id: str) -> str:
    return f"sites/{practice_id}/draft/{draft_id}/"


def preview_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class SiteDraft:
    file_count: int
    total_bytes: int
    uploaded_at: datetime
    #: What the draft's ``theme.json`` gives the portal; ``None`` without one.
    theme: ThemeReport | None = None


@dataclass(frozen=True)
class SiteVersion:
    version: int
    file_count: int
    total_bytes: int
    published_at: datetime
    published_by: str
    #: The portal theme this version gives while it is live.
    theme: PracticeTheme | None = None


@dataclass(frozen=True)
class SiteStatus:
    #: The version visitors are served once a website host works.
    live_version: int | None
    #: The working website host the live version is served at: the primary,
    #: else the oldest. ``None`` when nothing is published or no host works.
    live_host: str | None
    #: Whether the practice has a working website host at all.
    has_active_host: bool
    draft: SiteDraft | None
    #: Retained versions, newest first.
    versions: list[SiteVersion]


def _version(row: PracticeSiteVersionRow) -> SiteVersion:
    return SiteVersion(
        version=row.version,
        file_count=row.file_count,
        total_bytes=row.total_bytes,
        published_at=row.published_at,
        published_by=row.published_by,
        theme=stored_theme(row.theme),
    )


def _draft_theme(row: PracticeSiteRow) -> ThemeReport | None:
    return ThemeReport.model_validate(row.draft_theme) if row.draft_theme else None


def _draft(row: PracticeSiteRow | None) -> SiteDraft | None:
    if row is None or row.draft_id is None or row.draft_uploaded_at is None:
        return None
    return SiteDraft(
        file_count=row.draft_file_count or 0,
        total_bytes=row.draft_bytes or 0,
        uploaded_at=row.draft_uploaded_at,
        theme=_draft_theme(row),
    )


def _clear_draft(row: PracticeSiteRow) -> None:
    row.draft_id = None
    row.draft_file_count = None
    row.draft_bytes = None
    row.draft_uploaded_at = None
    row.draft_uploaded_by = None
    row.draft_theme = None
    row.preview_token_hash = None
    row.preview_expires_at = None


class PracticeSiteService:
    def __init__(
        self,
        store: PracticeSiteStore,
        storage: FileStorageProvider,
        bucket: str | None,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._store = store
        self._storage = storage
        self._bucket = bucket
        self._audit = audit
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return bool(self._bucket)

    def _require_bucket(self) -> str:
        if not self._bucket:
            raise SiteNotConfiguredError
        return self._bucket

    def status(self, practice_id: str) -> SiteStatus:
        row = self._store.get(practice_id)
        hosts = self._store.active_site_hosts(practice_id)
        live = row.live_version if row else None
        return SiteStatus(
            live_version=live,
            live_host=hosts[0].domain if hosts and live is not None else None,
            has_active_host=bool(hosts),
            draft=_draft(row),
            versions=[_version(v) for v in self._store.versions(practice_id)] if row else [],
        )

    # --- Drafts -----------------------------------------------------------

    def save_draft(
        self, practice_id: str, site: SiteFiles, user: User, request: Request | None = None
    ) -> SiteDraft:
        """Make *site* the practice's draft, replacing any draft it had."""
        self._require_bucket()
        now = self._clock()
        row = self._store.lock(practice_id, now)
        draft_id = uuid.uuid4().hex
        theme = read_theme(site.files.get(THEME_FILE))
        self._write(draft_prefix(practice_id, draft_id), site.files)
        row.draft_id = draft_id
        row.draft_file_count = site.file_count
        row.draft_bytes = site.total_bytes
        row.draft_uploaded_at = now
        row.draft_uploaded_by = user.id
        row.draft_theme = theme.model_dump(mode="json") if theme else None
        row.preview_token_hash = None
        row.preview_expires_at = None
        row.updated_at = now
        self._log(
            AuditAction.PRACTICE_SITE_DRAFT_SAVED,
            user,
            request,
            practice_id,
            {"file_count": site.file_count, "total_bytes": site.total_bytes},
        )
        return SiteDraft(
            file_count=site.file_count, total_bytes=site.total_bytes, uploaded_at=now, theme=theme
        )

    def save_draft_files(
        self,
        practice_id: str,
        files: Mapping[str, bytes],
        user: User,
        request: Request | None = None,
    ) -> SiteDraft:
        """Make files made in memory the draft: relative path to bytes, held to
        the same rules as an uploaded zip (:func:`app.sites.files.check_files`)."""
        return self.save_draft(practice_id, check_files(files), user, request)

    def discard_draft(self, practice_id: str) -> None:
        """Forget the draft. Its files go when the site is next tidied."""
        self._require_bucket()
        row = self._store.lock(practice_id, self._clock())
        _clear_draft(row)
        row.updated_at = self._clock()

    def mint_preview(self, practice_id: str) -> tuple[str, datetime]:
        """A new token for the draft's preview address, and when it stops
        working. The previous token stops working now."""
        self._require_bucket()
        now = self._clock()
        row = self._store.lock(practice_id, now)
        if row.draft_id is None:
            raise NoDraftError
        token = secrets.token_urlsafe(32)
        row.preview_token_hash = preview_token_hash(token)
        row.preview_expires_at = now + PREVIEW_TTL
        row.updated_at = now
        return token, row.preview_expires_at

    # --- Publishing -------------------------------------------------------

    def publish_draft(
        self, practice_id: str, user: User, request: Request | None = None
    ) -> SiteVersion:
        """Copy the draft to a new version and make that version live."""
        bucket = self._require_bucket()
        now = self._clock()
        row = self._store.lock(practice_id, now)
        if row.draft_id is None:
            raise NoDraftError
        source = draft_prefix(practice_id, row.draft_id)
        number = row.next_version
        target = version_prefix(practice_id, number)
        # A folder for this number can only be left by a publish that failed
        # before it committed; it is overwritten from empty.
        self._delete_all(target)
        file_count, total = 0, 0
        theme: ThemeReport | None = None
        for name in self._storage.list_names(bucket=bucket, prefix=source):
            path = name.removeprefix(source)
            data = self._storage.download_bytes(bucket=bucket, object_name=name)
            self._storage.upload_bytes(
                bucket=bucket,
                object_name=f"{target}{path}",
                data=data,
                content_type=content_type_for(path) or _OCTET_STREAM,
            )
            if path == THEME_FILE:
                # Read again from the files being published, so the version
                # carries exactly what the rules make of them now.
                theme = read_theme(data)
            file_count += 1
            total += len(data)
        version = PracticeSiteVersionRow(
            practice_id=practice_id,
            version=number,
            file_count=file_count,
            total_bytes=total,
            published_at=now,
            published_by=user.id,
            theme=storable_theme(theme.theme if theme else None),
        )
        self._store.add_version(version)
        row.live_version = number
        row.next_version = number + 1
        row.published_at = now
        row.published_by = user.id
        _clear_draft(row)
        row.updated_at = now
        self._log(
            AuditAction.PRACTICE_SITE_PUBLISHED,
            user,
            request,
            practice_id,
            {"version": number, "file_count": file_count, "total_bytes": total},
        )
        return _version(version)

    def publish_files(
        self,
        practice_id: str,
        files: Mapping[str, bytes],
        user: User,
        request: Request | None = None,
    ) -> SiteVersion:
        """Publish files made in memory at once: saved as the draft, then published."""
        self.save_draft_files(practice_id, files, user, request)
        return self.publish_draft(practice_id, user, request)

    def roll_back(
        self, practice_id: str, version: int, user: User, request: Request | None = None
    ) -> SiteVersion:
        """Make a retained earlier (or later) version live again."""
        self._require_bucket()
        now = self._clock()
        row = self._store.lock(practice_id, now)
        found = self._store.version(practice_id, version)
        if found is None:
            raise UnknownVersionError
        previous = row.live_version
        row.live_version = version
        row.published_at = now
        row.published_by = user.id
        row.updated_at = now
        self._log(
            AuditAction.PRACTICE_SITE_ROLLED_BACK,
            user,
            request,
            practice_id,
            {
                "version": version,
                "previous_version": previous,
                "file_count": found.file_count,
                "total_bytes": found.total_bytes,
            },
        )
        return _version(found)

    # --- Tidying ----------------------------------------------------------

    def tidy(self, practice_id: str) -> None:
        """Drop versions past the newest :data:`RETAINED_VERSIONS` (never the
        live one) and every folder no row points at. The caller commits."""
        bucket = self._require_bucket()
        if self._store.get(practice_id) is None:
            return
        row = self._store.lock(practice_id, self._clock())
        numbers = [v.version for v in self._store.versions(practice_id)]
        kept = set(numbers[:RETAINED_VERSIONS])
        if row.live_version is not None:
            kept.add(row.live_version)
        self._store.delete_versions(practice_id, [n for n in numbers if n not in kept])
        keep = [version_prefix(practice_id, n) for n in kept]
        if row.draft_id is not None:
            keep.append(draft_prefix(practice_id, row.draft_id))
        for name in self._storage.list_names(bucket=bucket, prefix=site_prefix(practice_id)):
            if not name.startswith(tuple(keep)):
                self._storage.delete(bucket=bucket, object_name=name)

    # --- Helpers ----------------------------------------------------------

    def _write(self, prefix: str, files: Mapping[str, bytes]) -> None:
        bucket = self._require_bucket()
        for path, data in files.items():
            self._storage.upload_bytes(
                bucket=bucket,
                object_name=f"{prefix}{path}",
                data=data,
                content_type=content_type_for(path) or _OCTET_STREAM,
            )

    def _delete_all(self, prefix: str) -> None:
        bucket = self._require_bucket()
        for name in self._storage.list_names(bucket=bucket, prefix=prefix):
            self._storage.delete(bucket=bucket, object_name=name)

    def _log(
        self,
        action: AuditAction,
        user: User,
        request: Request | None,
        practice_id: str,
        changes: dict[str, int | None],
    ) -> None:
        if self._audit is None:
            return
        self._audit.log(
            action,
            user,
            request,
            resource_type=ResourceType.PRACTICE,
            resource_id=practice_id,
            changes={"practice_id": practice_id, **changes},
        )


def practice_site_service(session: Session, audit: AuditService | None) -> PracticeSiteService:
    """The service on *session*, with this deployment's bucket and storage.

    For a deployment's own publisher running outside a request: pass the
    session it will commit and the audit service it records with.
    """
    return PracticeSiteService(
        PracticeSiteStore(session), site_storage(), get_settings().practice_site_bucket, audit
    )


def get_practice_site_service(
    audit: AuditService = Depends(get_audit_service),
) -> PracticeSiteService:
    """FastAPI dependency — the service on the request's session."""
    from ..db import get_db_session  # noqa: PLC0415 — needs a request in flight

    return practice_site_service(get_db_session(), audit)


def tidy_practice_site(practice_id: str) -> None:
    """Tidy the practice's website on a session of its own, and commit.

    Run as a background task after a change, so it sees that change committed.
    """
    from ..db import create_standalone_session  # noqa: PLC0415

    if not get_settings().practice_site_bucket:
        return
    session = create_standalone_session()
    try:
        practice_site_service(session, None).tidy(practice_id)
        session.commit()
    finally:
        session.close()
