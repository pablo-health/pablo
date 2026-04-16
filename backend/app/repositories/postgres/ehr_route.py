# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""PostgreSQL EHR route repository implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...db.models import EhrRouteRow
from ...models.ehr_route import EhrRoute, EhrRouteStep
from ...utcnow import utc_now
from ..ehr_route import EhrRouteRepository

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class PostgresEhrRouteRepository(EhrRouteRepository):
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, ehr_system: str) -> EhrRoute | None:
        row = self._session.query(EhrRouteRow).filter_by(ehr_system=ehr_system).first()
        if row is None:
            return None
        return _row_to_route(row)

    def upsert(self, route: EhrRoute) -> EhrRoute:
        now = utc_now()
        if not route.created_at:
            route.created_at = now
        route.updated_at = now
        row = self._session.get(EhrRouteRow, route.id)
        if row is None:
            row = EhrRouteRow(id=route.id)
            self._session.add(row)
        row.ehr_system = route.ehr_system
        row.route_name = route.route_name
        row.steps = [s.to_dict() for s in route.steps]
        row.success_count = route.success_count
        row.last_success = route.last_success
        row.created_at = route.created_at
        row.updated_at = route.updated_at
        self._session.flush()
        return route

    def update_step(
        self,
        ehr_system: str,
        step_index: int,
        selector: str,
        a11y_fingerprint: str,
    ) -> EhrRoute | None:
        row = self._session.query(EhrRouteRow).filter_by(ehr_system=ehr_system).first()
        if row is None:
            return None
        steps = list(row.steps)
        if step_index < 0 or step_index >= len(steps):
            msg = f"Step index {step_index} out of range (0-{len(steps) - 1})"
            raise IndexError(msg)
        steps[step_index]["selector"] = selector
        steps[step_index]["a11y_fingerprint"] = a11y_fingerprint
        now = utc_now()
        row.steps = steps
        row.updated_at = now
        self._session.flush()
        return _row_to_route(row)

    def increment_success(self, ehr_system: str) -> None:
        row = self._session.query(EhrRouteRow).filter_by(ehr_system=ehr_system).first()
        if row:
            row.success_count += 1
            row.last_success = utc_now()
            self._session.flush()


def _row_to_route(row: EhrRouteRow) -> EhrRoute:
    return EhrRoute(
        id=row.id,
        ehr_system=row.ehr_system,
        route_name=row.route_name,
        steps=[EhrRouteStep.from_dict(s) for s in row.steps],
        success_count=row.success_count,
        last_success=row.last_success,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )
