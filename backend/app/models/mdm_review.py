# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""API models for the medical decision making review beside a prescriber's note."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from ..notes.mdm_review import RATIONALE_ORDER

if TYPE_CHECKING:
    from ..notes.mdm_review import MdmReview

Element = Literal["problems", "data", "risk"]


class MdmChoicesRequest(BaseModel):
    """The clinician's choices; a blank one is cleared."""

    problems: str | None = None
    data: str | None = None
    risk: str | None = None
    new_patient: Literal["new", "established"] | None = None


class MdmElementResponse(BaseModel):
    """One element: what was chosen, the drafted evidence, and its part in the level."""

    element: Element
    chosen: str | None
    evidence: str
    #: The level the computed MDM level needed from this element; null until all three are chosen.
    required: str | None = None
    meets: bool | None = None


class MdmReviewResponse(BaseModel):
    #: Elements in the order the panel shows them: problems and risk first.
    elements: list[MdmElementResponse]
    new_patient: bool
    level: str | None
    em_code: str | None
    has_psychotherapy: bool
    psychotherapy_minutes: int | None
    add_on: str | None
    #: False while a psychotherapy portion has no confirmed minutes.
    add_on_known: bool
    #: How the E/M level may be chosen: by MDM only once a psychotherapy add-on is billed.
    billing_methods: list[Literal["mdm", "time"]]
    dictated_em_code: str | None
    dictated_add_on: str | None
    em_disagrees: bool
    add_on_disagrees: bool
    #: The note's visit details with the computed codes in place; null when that changes nothing.
    visit_details_with_codes: str | None

    @staticmethod
    def from_review(review: MdmReview) -> MdmReviewResponse:
        rows = {r.element: r for r in review.rationale}
        chosen = {
            "problems": review.choices.problems,
            "data": review.choices.data,
            "risk": review.choices.risk,
        }
        return MdmReviewResponse(
            elements=[
                MdmElementResponse(
                    element=element,
                    chosen=chosen[element],
                    evidence=review.evidence[element],
                    required=rows[element].required if element in rows else None,
                    meets=rows[element].meets if element in rows else None,
                )
                for element in RATIONALE_ORDER
            ],
            new_patient=review.choices.new_patient,
            level=review.level,
            em_code=review.em_code,
            has_psychotherapy=review.has_psychotherapy,
            psychotherapy_minutes=review.psychotherapy_minutes,
            add_on=review.add_on,
            add_on_known=review.add_on_known,
            billing_methods=list(review.billing_methods),
            dictated_em_code=review.dictated_em_code,
            dictated_add_on=review.dictated_add_on,
            em_disagrees=review.em_disagrees,
            add_on_disagrees=review.add_on_disagrees,
            visit_details_with_codes=review.visit_details_with_codes,
        )
