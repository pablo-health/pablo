# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where the domain reconciler reads hosts and writes what it found.

The hosts live in the shared ``platform`` schema; the audit trail lives in each
practice's own schema. So a practice's unit of work is a tenant session for
that practice, armed as the practice's owner — the platform tables are
schema-qualified and reachable from it, and the audit row and the status it
records commit together.

A practice with no row any more, or offboarded with its schema dropped, still
has its hosts taken down: its unit of work is a plain platform session, and an
audit event has nowhere to go, which is logged instead.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from sqlalchemy import exists, or_, select, update

from ..db import create_standalone_session
from ..db.platform_models import (
    PracticeDomainRow,
    PracticeRow,
    PracticeSiteRow,
    PracticeSiteVersionRow,
)
from ..db.tenant_session import tenant_db_session
from ..models.audit import ResourceType
from ..models.practice_domain import IN_PROGRESS_STATUSES
from ..portal.hosted import hosted_addresses_ready, hosted_site_host, practice_slug
from ..repositories.postgres.audit import PostgresAuditRepository
from ..repositories.postgres.practice_domain import PostgresPracticeDomainRepository
from .audit_service import AuditService
from .practice_domain_reconciler import SiteServing

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from sqlalchemy.orm import Session

    from ..models.audit import AuditAction
    from ..models.practice_domain import PracticeDomain
    from ..repositories.practice_domain import PracticeDomainRepository

logger = logging.getLogger(__name__)

#: The ``actor_component`` on every audit row the reconciler writes.
ACTOR_COMPONENT = "practice_domain_reconciler"
#: Who a row is scoped to when the practice has no owner on record.
UNOWNED_SCOPE = "system:practice-domains"


class _Scope:
    def __init__(
        self,
        repo: PracticeDomainRepository,
        audit: AuditService | None,
        practice_id: str,
        scope_user_id: str,
    ) -> None:
        self._repo = repo
        self._audit = audit
        self._practice_id = practice_id
        self._scope_user_id = scope_user_id

    @property
    def repo(self) -> PracticeDomainRepository:
        return self._repo

    def audit(self, action: AuditAction, changes: dict[str, Any]) -> None:
        if self._audit is None:
            logger.warning(
                "practice_domain_reconcile_unaudited practice_id=%s action=%s: no such practice",
                self._practice_id,
                action.value,
            )
            return
        self._audit.log_system_action(
            action,
            scope_user_id=self._scope_user_id,
            resource_type=ResourceType.PRACTICE,
            resource_id=self._practice_id,
            actor_component=ACTOR_COMPONENT,
            changes=changes,
        )


def _hosted_host(session: Session, practice_id: str) -> str | None:
    """The practice's hosted website address, where the deployment serves one."""
    if not hosted_addresses_ready():
        return None
    slug = practice_slug(session, practice_id)
    return hosted_site_host(slug) if slug else None


class PostgresReconcileStore:
    def all_hosts(self) -> list[PracticeDomain]:
        session = create_standalone_session()
        try:
            return PostgresPracticeDomainRepository(session).list_all()
        finally:
            session.close()

    def any_in_progress(self) -> bool:
        session = create_standalone_session()
        try:
            return bool(
                session.execute(
                    select(exists().where(PracticeDomainRow.status.in_(IN_PROGRESS_STATUSES)))
                ).scalar()
            )
        finally:
            session.close()

    def site(self, practice_id: str) -> SiteServing | None:
        session = create_standalone_session()
        try:
            row = session.get(PracticeSiteRow, practice_id)
            if row is None:
                return None
            retained = session.execute(
                select(PracticeSiteVersionRow.version).where(
                    PracticeSiteVersionRow.practice_id == practice_id
                )
            ).scalars()
            return SiteServing(
                live_version=row.live_version,
                served_version=row.served_version,
                hide_from_search=row.hide_from_search,
                served_hide_from_search=row.served_hide_from_search,
                retained=tuple(sorted(retained)),
                hosted_host=_hosted_host(session, practice_id),
                served_hosted_host=row.served_hosted_host,
            )
        finally:
            session.close()

    def record_served(
        self,
        practice_id: str,
        version: int,
        *,
        hide_from_search: bool,
        hosted_host: str | None,
    ) -> None:
        session = create_standalone_session()
        try:
            session.execute(
                update(PracticeSiteRow)
                .where(
                    PracticeSiteRow.practice_id == practice_id,
                    PracticeSiteRow.live_version == version,
                    PracticeSiteRow.hide_from_search.is_(hide_from_search),
                )
                .values(
                    served_version=version,
                    served_hide_from_search=hide_from_search,
                    served_hosted_host=hosted_host,
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def clear_served_hosted_host(self, practice_id: str) -> None:
        session = create_standalone_session()
        try:
            session.execute(
                update(PracticeSiteRow)
                .where(PracticeSiteRow.practice_id == practice_id)
                .values(served_hosted_host=None)
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def any_site_behind(self) -> bool:
        session = create_standalone_session()
        try:
            served = select(PracticeDomainRow.practice_id).where(
                PracticeDomainRow.practice_id == PracticeSiteRow.practice_id,
                PracticeDomainRow.purpose == "site",
                PracticeDomainRow.status == "active",
            )
            behind = select(
                exists().where(
                    PracticeSiteRow.live_version.is_not(None),
                    or_(
                        PracticeSiteRow.live_version.is_distinct_from(
                            PracticeSiteRow.served_version
                        ),
                        PracticeSiteRow.hide_from_search.is_distinct_from(
                            PracticeSiteRow.served_hide_from_search
                        ),
                    ),
                    or_(exists(served), PracticeSiteRow.served_hosted_host.is_not(None)),
                )
            )
            if session.execute(behind).scalar():
                return True
            if not hosted_addresses_ready():
                # No hosted address can be routed; one still recorded is behind.
                return bool(
                    session.execute(
                        select(exists().where(PracticeSiteRow.served_hosted_host.is_not(None)))
                    ).scalar()
                )
            rows = session.execute(
                select(PracticeSiteRow.practice_id, PracticeSiteRow.served_hosted_host).where(
                    PracticeSiteRow.live_version.is_not(None)
                )
            ).all()
            return any(_hosted_host(session, pid) != served for pid, served in rows)
        finally:
            session.close()

    def practices_with_sites(self) -> list[str]:
        session = create_standalone_session()
        try:
            return list(
                session.execute(
                    select(PracticeSiteRow.practice_id).where(
                        PracticeSiteRow.live_version.is_not(None)
                    )
                ).scalars()
            )
        finally:
            session.close()

    def retired_practices(self, practice_ids: Collection[str]) -> set[str]:
        """The practices whose hosts should stop being served: deactivated
        (``is_active`` false), offboarded (``deleted_at`` set), or with no row.
        The same test the public booking page applies to a practice."""
        session = create_standalone_session()
        try:
            live = set(
                session.execute(
                    select(PracticeRow.id).where(
                        PracticeRow.id.in_(list(practice_ids)),
                        PracticeRow.is_active.is_(True),
                        PracticeRow.deleted_at.is_(None),
                    )
                ).scalars()
            )
        finally:
            session.close()
        return set(practice_ids) - live

    @contextmanager
    def practice(self, practice_id: str) -> Iterator[_Scope]:
        session = create_standalone_session()
        try:
            practice = session.get(PracticeRow, practice_id)
            # An offboarded practice's schema has been dropped.
            present = practice is not None and practice.deleted_at is None
            schema = practice.schema_name if present and practice is not None else None
            owner = practice.owner_user_id if practice else None
        finally:
            session.close()

        if schema is None:
            session = create_standalone_session()
            try:
                yield _Scope(PostgresPracticeDomainRepository(session), None, practice_id, "")
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
            return

        scope_user_id = owner or UNOWNED_SCOPE
        with tenant_db_session(schema, scope_user_id) as tenant:
            yield _Scope(
                PostgresPracticeDomainRepository(tenant),
                AuditService(PostgresAuditRepository(tenant)),
                practice_id,
                scope_user_id,
            )
