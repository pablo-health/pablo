# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""EHR navigation route models.

Navigation metadata only — no PHI is stored in these models.
The companion app strips patient names before calling the LLM fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .enums import EhrSystem  # noqa: TC001 — Pydantic needs these at runtime

# ---------------------------------------------------------------------------
# Domain dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EhrRouteStep:
    """Single navigation step in an EHR route."""

    action: str
    selector: str
    a11y_fingerprint: str
    intent: str
    dynamic_key: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EhrRouteStep:
        """Create from dictionary."""
        return cls(
            action=data["action"],
            selector=data["selector"],
            a11y_fingerprint=data["a11y_fingerprint"],
            intent=data["intent"],
            dynamic_key=data.get("dynamic_key"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        d: dict[str, Any] = {
            "action": self.action,
            "selector": self.selector,
            "a11y_fingerprint": self.a11y_fingerprint,
            "intent": self.intent,
        }
        if self.dynamic_key is not None:
            d["dynamic_key"] = self.dynamic_key
        return d


@dataclass
class EhrRoute:
    """Complete navigation route for an EHR system.

    Shared across all therapists on the same EHR within a tenant.
    Primary key = ehr_system value.
    """

    id: str
    ehr_system: str
    route_name: str
    steps: list[EhrRouteStep] = field(default_factory=list)
    success_count: int = 0
    last_success: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EhrRoute:
        """Create from dictionary."""
        return cls(
            id=data["id"],
            ehr_system=data["ehr_system"],
            route_name=data["route_name"],
            steps=[EhrRouteStep.from_dict(s) for s in data.get("steps", [])],
            success_count=data.get("success_count", 0),
            last_success=data.get("last_success"),
            created_at=data.get("created_at"),
            updated_at=data.get("updated_at"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        d: dict[str, Any] = {
            "id": self.id,
            "ehr_system": self.ehr_system,
            "route_name": self.route_name,
            "steps": [s.to_dict() for s in self.steps],
            "success_count": self.success_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.last_success is not None:
            d["last_success"] = self.last_success
        return d


# ---------------------------------------------------------------------------
# Pydantic request/response models (API contract with Swift companion app)
# ---------------------------------------------------------------------------


class EhrRouteStepResponse(BaseModel):
    """A single step in the cached navigation route."""

    action: str
    selector: str
    a11y_fingerprint: str
    intent: str
    dynamic_key: str | None = None


class EhrRouteResponse(BaseModel):
    """GET /api/ehr-routes/{ehr_system} response."""

    ehr_system: str
    route_name: str
    steps: list[EhrRouteStepResponse]
    success_count: int
    last_success: datetime | None = None

    @classmethod
    def from_ehr_route(cls, route: EhrRoute) -> EhrRouteResponse:
        """Create response from domain model."""
        return cls(
            ehr_system=route.ehr_system,
            route_name=route.route_name,
            steps=[
                EhrRouteStepResponse(
                    action=s.action,
                    selector=s.selector,
                    a11y_fingerprint=s.a11y_fingerprint,
                    intent=s.intent,
                    dynamic_key=s.dynamic_key,
                )
                for s in route.steps
            ],
            success_count=route.success_count,
            last_success=route.last_success,
        )


class UpdateEhrRouteStepRequest(BaseModel):
    """PATCH body — companion reports a new working selector after LLM recovery."""

    selector: str = Field(max_length=500)
    a11y_fingerprint: str = Field(max_length=500)


class PreviousAction(BaseModel):
    """A single action previously taken during goal navigation."""

    action: str
    target: str
    result: str


class GoalNavigationRequest(BaseModel):
    """POST /api/ehr-navigate — goal-based LLM navigation (PHI stripped by client)."""

    ehr_system: EhrSystem
    goal: str = Field(max_length=500)
    current_url: str = Field(max_length=2000)
    dom_snapshot: str = Field(max_length=50_000)
    previous_actions: list[PreviousAction] = Field(default_factory=list, max_length=20)
    failed_action: str | None = Field(default=None, max_length=500)


class GoalNavigationResponse(BaseModel):
    """POST /api/ehr-navigate response — next navigation action from LLM."""

    action: Literal["click", "navigate", "wait", "fill", "none"]
    selector: str
    reasoning: str
    confidence: float = Field(ge=0.0, le=1.0)
    is_on_target_page: bool
    form_fields: dict[str, str] | None = None
    alternative_plan: str | None = None
