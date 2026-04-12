# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""EHR route repository implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from ..models.ehr_route import EhrRoute
from ..utcnow import utc_now_iso

if TYPE_CHECKING:
    from google.cloud.firestore import Client as FirestoreClient

EHR_ROUTES_COLLECTION = "ehr_routes"


class EhrRouteRepository(ABC):
    """Abstract base class for EHR route data access."""

    @abstractmethod
    def get(self, ehr_system: str) -> EhrRoute | None:
        """Get route by EHR system name."""

    @abstractmethod
    def upsert(self, route: EhrRoute) -> EhrRoute:
        """Create or update an EHR route."""

    @abstractmethod
    def update_step(
        self,
        ehr_system: str,
        step_index: int,
        selector: str,
        a11y_fingerprint: str,
    ) -> EhrRoute | None:
        """Update a specific step's selector and fingerprint.

        Returns the updated route, or None if the route doesn't exist.
        Raises IndexError if step_index is out of range.
        """

    @abstractmethod
    def increment_success(self, ehr_system: str) -> None:
        """Increment success count and update last_success timestamp."""


class FirestoreEhrRouteRepository(EhrRouteRepository):
    """Firestore implementation of EhrRouteRepository."""

    def __init__(self, db: FirestoreClient) -> None:
        self._db = db

    def _collection(self) -> Any:
        return self._db.collection(EHR_ROUTES_COLLECTION)

    def get(self, ehr_system: str) -> EhrRoute | None:
        doc = self._collection().document(ehr_system).get()
        if not doc.exists:
            return None
        data = doc.to_dict()
        data["id"] = doc.id
        return EhrRoute.from_dict(data)

    def upsert(self, route: EhrRoute) -> EhrRoute:
        now = utc_now_iso()
        if not route.created_at:
            route.created_at = now
        route.updated_at = now
        self._collection().document(route.id).set(route.to_dict())
        return route

    def update_step(
        self,
        ehr_system: str,
        step_index: int,
        selector: str,
        a11y_fingerprint: str,
    ) -> EhrRoute | None:
        route = self.get(ehr_system)
        if route is None:
            return None
        if step_index < 0 or step_index >= len(route.steps):
            msg = f"Step index {step_index} out of range (0-{len(route.steps) - 1})"
            raise IndexError(msg)

        route.steps[step_index].selector = selector
        route.steps[step_index].a11y_fingerprint = a11y_fingerprint

        now = utc_now_iso()
        route.updated_at = now
        self._collection().document(ehr_system).update(
            {
                "steps": [s.to_dict() for s in route.steps],
                "updated_at": now,
            }
        )
        return route

    def increment_success(self, ehr_system: str) -> None:
        from google.cloud.firestore import Increment  # type: ignore[attr-defined]  # noqa: PLC0415

        now = utc_now_iso()
        self._collection().document(ehr_system).update(
            {
                "success_count": Increment(1),
                "last_success": now,
            }
        )


class InMemoryEhrRouteRepository(EhrRouteRepository):
    """In-memory implementation for testing."""

    def __init__(self) -> None:
        self._routes: dict[str, EhrRoute] = {}

    def get(self, ehr_system: str) -> EhrRoute | None:
        return self._routes.get(ehr_system)

    def upsert(self, route: EhrRoute) -> EhrRoute:
        now = utc_now_iso()
        if not route.created_at:
            route.created_at = now
        route.updated_at = now
        self._routes[route.id] = route
        return route

    def update_step(
        self,
        ehr_system: str,
        step_index: int,
        selector: str,
        a11y_fingerprint: str,
    ) -> EhrRoute | None:
        route = self._routes.get(ehr_system)
        if route is None:
            return None
        if step_index < 0 or step_index >= len(route.steps):
            msg = f"Step index {step_index} out of range (0-{len(route.steps) - 1})"
            raise IndexError(msg)

        route.steps[step_index].selector = selector
        route.steps[step_index].a11y_fingerprint = a11y_fingerprint
        route.updated_at = utc_now_iso()
        return route

    def increment_success(self, ehr_system: str) -> None:
        route = self._routes.get(ehr_system)
        if route:
            route.success_count += 1
            route.last_success = utc_now_iso()

    def seed(self, route: EhrRoute) -> None:
        """Seed test data without modifying timestamps."""
        self._routes[route.id] = route
