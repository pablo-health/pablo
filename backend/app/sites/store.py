# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The rows behind a practice's website: ``platform.practice_sites`` and its versions.

Every query names the practice, and both tables are keyed on it. The website
hosts are read from ``platform.practice_domains`` (purpose ``site``), and its
hosted address from its portal slug (:mod:`app.portal.hosted`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from ..db.platform_models import PracticeDomainRow, PracticeSiteRow, PracticeSiteVersionRow
from ..portal.hosted import hosted_site_host, practice_slug

if TYPE_CHECKING:
    from collections.abc import Collection
    from datetime import datetime

    from sqlalchemy.orm import Session


class PracticeSiteStore:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, practice_id: str) -> PracticeSiteRow | None:
        return self._session.get(PracticeSiteRow, practice_id)

    def lock(self, practice_id: str, now: datetime) -> PracticeSiteRow:
        """The practice's row, made if it has none, locked until the transaction ends."""
        self._session.execute(
            insert(PracticeSiteRow)
            .values(practice_id=practice_id, next_version=1, updated_at=now)
            .on_conflict_do_nothing(index_elements=["practice_id"])
        )
        stmt = (
            select(PracticeSiteRow)
            .where(PracticeSiteRow.practice_id == practice_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return self._session.execute(stmt).scalar_one()

    def versions(self, practice_id: str) -> list[PracticeSiteVersionRow]:
        """The retained versions, newest first."""
        stmt = (
            select(PracticeSiteVersionRow)
            .where(PracticeSiteVersionRow.practice_id == practice_id)
            .order_by(PracticeSiteVersionRow.version.desc())
        )
        return list(self._session.execute(stmt).scalars())

    def version(self, practice_id: str, version: int) -> PracticeSiteVersionRow | None:
        return self._session.get(PracticeSiteVersionRow, (practice_id, version))

    def add_version(self, row: PracticeSiteVersionRow) -> None:
        self._session.add(row)
        self._session.flush()

    def delete_versions(self, practice_id: str, versions: Collection[int]) -> None:
        if not versions:
            return
        self._session.execute(
            delete(PracticeSiteVersionRow).where(
                PracticeSiteVersionRow.practice_id == practice_id,
                PracticeSiteVersionRow.version.in_(list(versions)),
            )
        )

    def by_preview_token_hash(self, token_hash: str) -> PracticeSiteRow | None:
        stmt = select(PracticeSiteRow).where(PracticeSiteRow.preview_token_hash == token_hash)
        return self._session.execute(stmt).scalar_one_or_none()

    def active_site_hosts(self, practice_id: str) -> list[PracticeDomainRow]:
        """The practice's working website hosts, the primary first, then oldest first."""
        stmt = (
            select(PracticeDomainRow)
            .where(
                PracticeDomainRow.practice_id == practice_id,
                PracticeDomainRow.purpose == "site",
                PracticeDomainRow.status == "active",
            )
            .order_by(
                PracticeDomainRow.is_primary.desc(),
                PracticeDomainRow.created_at,
                PracticeDomainRow.domain,
            )
        )
        return list(self._session.execute(stmt).scalars())

    def hosted_site_host(self, practice_id: str) -> str | None:
        """The practice's hosted website address, where the deployment has one."""
        slug = practice_slug(self._session, practice_id)
        return hosted_site_host(slug) if slug else None
