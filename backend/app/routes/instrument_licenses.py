# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The instrument catalogue, and the permission a practice records for one.

Some instruments may be reproduced but not used freely. The registry says
which (see :mod:`app.outcome_measures.instruments`), and these routes are
what a practice uses to record that it holds the permission a restricted one
requires, plus the one read both the settings screen and the form builder
work from.

**Nothing here is a chart.** Like the form builder it sits beside, no row
belongs to a patient: a licence is held by the practice, the same licence
whoever is asked. The routes carry the practice's ordinary credential — an
accepted agreement and a tenant context — and take no patient id. There is
no disclosure to audit because there is nothing about anybody to disclose.

**Both writes are audited, and neither carries anything but a code.** Which
instruments a practice claims permission for, and when it withdrew one, is a
record worth having: it is what a form published afterwards was allowed to
ask. The instrument code is the whole payload — the licence reference is the
practice's own note and stays on the row.

A ``404`` means there was no permission in force to withdraw. A ``422``
means the instrument is not one permission can be recorded for: the code is
unknown, or the registry already treats it as free to use, or it is a form
this engine does not carry and no attestation would change that.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Request, status

from ..api_errors import NotFoundError, UnprocessableEntityError
from ..auth.service import TenantContext, get_tenant_context, require_baa_acceptance
from ..intake.items import SELF_REPORT_INSTRUMENTS
from ..models import User  # noqa: TC001 — fastapi resolves the annotation at runtime
from ..models.audit import AuditAction, ResourceType
from ..models.instrument_api import (
    AttestInstrumentRequest,
    InstrumentAttestationResponse,
    InstrumentResponse,
)
from ..outcome_measures.instruments import INSTRUMENT_REGISTRY
from ..repositories import get_instrument_license_repository
from ..services.audit_service import AuditService, get_audit_service
from ..services.instrument_license_service import (
    InstrumentLicenseService,
    UnlicensableInstrumentError,
)

if TYPE_CHECKING:
    from ..repositories.instrument_license import InstrumentLicenseRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/intake/instruments", tags=["instrument-licenses"])


def get_instrument_license_service(
    _ctx: TenantContext = Depends(get_tenant_context),
) -> InstrumentLicenseService:
    """The licence service on a tenant-scoped session.

    The tenant context is the whole isolation story for this surface: every
    query underneath runs against one practice's schema, and no row here
    belongs to a narrower owner than the practice.
    """
    repo: InstrumentLicenseRepository = get_instrument_license_repository()
    return InstrumentLicenseService(repo)


LicenseService = Annotated[InstrumentLicenseService, Depends(get_instrument_license_service)]


def _attestation_response(row: dict[str, object]) -> InstrumentAttestationResponse:
    return InstrumentAttestationResponse(
        id=str(row["id"]),
        instrument_code=str(row["instrument_code"]),
        attested_at=row["attested_at"],  # type: ignore[arg-type]
        license_reference=_optional_str(row.get("license_reference")),
        notes=_optional_str(row.get("notes")),
        revoked_at=row.get("revoked_at"),  # type: ignore[arg-type]
    )


def _optional_str(value: object) -> str | None:
    """A nullable text column read back off a row that hands back ``object``."""
    return value if isinstance(value, str) else None


@router.get("", response_model=list[InstrumentResponse])
def list_instruments(
    service: LicenseService,
    _user: User = Depends(require_baa_acceptance),
) -> list[InstrumentResponse]:
    """Every instrument the engine knows, and what this practice may do with it.

    One list for two screens. The settings screen reads the restricted ones
    and what has been attested; the form builder reads which are askable and
    whether the practice is allowed to ask them yet.
    """
    held = {str(row["instrument_code"]): row for row in service.list_active()}
    return [
        InstrumentResponse(
            code=code,
            display_name=defn.display_name,
            rights=defn.rights,
            rights_note=defn.rights_note,
            publisher_url=defn.publisher_url,
            item_count=defn.item_count,
            can_ask_on_a_form=code in SELF_REPORT_INSTRUMENTS,
            attested=code in held,
            attested_at=held[code]["attested_at"] if code in held else None,  # type: ignore[arg-type]
            license_reference=(
                _optional_str(held[code].get("license_reference")) if code in held else None
            ),
        )
        for code, defn in sorted(INSTRUMENT_REGISTRY.items(), key=lambda kv: kv[1].display_name)
    ]


@router.post(
    "/{instrument_code}/attestation",
    response_model=InstrumentAttestationResponse,
    status_code=status.HTTP_201_CREATED,
)
def attest_instrument(
    instrument_code: str,
    body: AttestInstrumentRequest,
    service: LicenseService,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> InstrumentAttestationResponse:
    """Record that this practice holds the permission this instrument requires."""
    try:
        recorded = service.attest(
            instrument_code,
            user.id,
            license_reference=body.license_reference,
            notes=body.notes,
        )
    except UnlicensableInstrumentError as exc:
        raise UnprocessableEntityError(
            "There is no permission to record for this instrument.",
            {"instrument_code": instrument_code},
        ) from exc

    audit.log(
        action=AuditAction.INSTRUMENT_LICENSE_ATTESTED,
        user=user,
        request=request,
        resource_type=ResourceType.INSTRUMENT_LICENSE_ATTESTATION,
        resource_id=str(recorded["id"]),
        changes={"instrument_code": instrument_code},
    )
    return _attestation_response(recorded)


@router.delete(
    "/{instrument_code}/attestation",
    response_model=InstrumentAttestationResponse,
)
def revoke_instrument_attestation(
    instrument_code: str,
    service: LicenseService,
    request: Request,
    user: User = Depends(require_baa_acceptance),
    audit: AuditService = Depends(get_audit_service),
) -> InstrumentAttestationResponse:
    """Withdraw the permission in force for this instrument.

    Forms already published that ask it keep working. Withdrawing says what
    may go on a new form; a published version is what somebody's answers
    were answers to.
    """
    withdrawn = service.revoke(instrument_code)
    if withdrawn is None:
        raise NotFoundError(
            "No permission is recorded for this instrument.",
            {"instrument_code": instrument_code},
        )

    audit.log(
        action=AuditAction.INSTRUMENT_LICENSE_REVOKED,
        user=user,
        request=request,
        resource_type=ResourceType.INSTRUMENT_LICENSE_ATTESTATION,
        resource_id=str(withdrawn["id"]),
        changes={"instrument_code": instrument_code},
    )
    return _attestation_response(withdrawn)
