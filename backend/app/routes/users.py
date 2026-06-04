# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
User API routes.

Implements user profile management and BAA (Business Associate Agreement) acceptance.
"""

import logging
import re
from datetime import datetime
from pathlib import Path

import google.auth
import google.auth.transport.requests
import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from ..api_errors import BadRequestError, NotFoundError, ServerError
from ..auth.route_security import truly_public
from ..auth.service import (
    TenantContext,
    get_baa_version,
    get_current_user,
    get_current_user_no_mfa,
    get_tenant_context,
)
from ..models import (
    AcceptBAARequest,
    AcknowledgeSecurityGuideRequest,
    BAAStatusResponse,
    SecurityGuideStatusResponse,
    UpdateProfessionalInfoRequest,
    UpdateThemeRequest,
    UpdateUserRequest,
    User,
    UserPreferences,
)
from ..models.audit import AuditAction
from ..repositories import (
    ClinicianProfile,
    ClinicianProfileRepository,
    IdentityRepository,
    UserRepository,
    get_clinician_profile_repository,
    get_identity_repository,
    get_user_repository,
)
from ..services import AuditService, get_audit_service
from ..utcnow import utc_now, utc_now_iso

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users", tags=["users"])


_IDENTITY_TOOLKIT_LOOKUP_URL = "https://identitytoolkit.googleapis.com/v1/accounts:lookup"


def _user_has_totp_factor(firebase_uid: str) -> bool:
    """Return True iff the Firebase user has at least one TOTP factor enrolled.

    The Python ``firebase_admin`` SDK (every version through 7.4.0 as of
    2026-05-19) does not expose multi-factor information on ``UserRecord``
    — it's a Node.js-only feature. We query Identity Toolkit's
    ``accounts:lookup`` REST endpoint directly and parse ``mfaInfo[]``
    for a factor with a ``totpInfo`` block.

    Uses Application Default Credentials, which on Cloud Run resolves
    to the service's runtime identity. That identity is the same project
    as the Firebase project, so the call is in-project and authorized
    by default — no extra IAM bindings needed.

    Raises on transport / non-2xx errors so the caller surfaces a 5xx
    — a failed lookup is a server-side problem, not a client error.
    """
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    credentials.refresh(google.auth.transport.requests.Request())

    response = httpx.post(
        _IDENTITY_TOOLKIT_LOOKUP_URL,
        headers={"Authorization": f"Bearer {credentials.token}"},
        json={"localId": [firebase_uid]},
        timeout=10.0,
    )
    response.raise_for_status()
    payload = response.json()
    users = payload.get("users") or []
    if not users:
        # Authenticated user that Identity Toolkit can't find is a hard
        # invariant violation — let the caller convert to a 5xx.
        raise LookupError(f"Identity Toolkit returned no user for firebase_uid={firebase_uid}")
    return any(factor.get("totpInfo") is not None for factor in users[0].get("mfaInfo") or [])


BAA_DIR = (Path(__file__).parent.parent.parent / "baa").resolve()
BAA_VERSION_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _get_available_baa_files() -> dict[str, Path]:
    """Scan BAA directory and return a map of version → resolved path."""
    result: dict[str, Path] = {}
    for path in BAA_DIR.glob("BAA-*.md"):
        version = path.stem.removeprefix("BAA-")
        if BAA_VERSION_PATTERN.match(version):
            result[version] = path.resolve()
    return result


def _resolve_baa_path(version: str) -> Path:
    """Validate a BAA version string and return the resolved file path.

    Raises HTTPException 400/404 if the version format is invalid or
    no matching BAA file exists on disk.
    """
    if not BAA_VERSION_PATTERN.match(version):
        raise BadRequestError(
            "BAA version must be a date in YYYY-MM-DD format",
            {"version": version},
            code="INVALID_VERSION",
        )

    available = _get_available_baa_files()
    baa_path = available.get(version)
    if baa_path is None:
        raise NotFoundError(f"BAA version {version} not found", {"version": version})

    return baa_path


@router.get("/me/status")
def get_user_status(
    user: User = Depends(get_current_user_no_mfa),
    profile_repo: ClinicianProfileRepository = Depends(get_clinician_profile_repository),
) -> dict:
    """
    Get current user status without requiring MFA.

    Used by dashboard layout and companion app to check MFA enrollment
    and subscription/trial status.
    """
    from ..settings import get_settings

    profile = profile_repo.get(user.id)

    result: dict = {
        "status": user.status,
        "mfa_enrolled_at": user.mfa_enrolled_at,
        "is_platform_admin": user.is_platform_admin,
        "name": user.name,
        "email": user.email,
        "title": profile.title if profile else None,
        "credentials": profile.credentials if profile else None,
        "legal_name": user.legal_name,
        "license_number": profile.license_number if profile else None,
        "license_state": profile.license_state if profile else None,
        "provider_type": user.provider_type,
        "security_guide_acknowledged_at": user.security_guide_acknowledged_at,
        "security_guide_version": user.security_guide_version,
        "onboarding_state": user.onboarding_state,
        # Exposed pre-MFA so the SaaS onboarding wizard step registry can
        # gate on it synchronously (see pablo-saas
        # frontend/overlay/src/lib/onboarding/steps.ts). Version-aware
        # re-prompt is still driven by /me/baa-status.
        "baa_accepted_at": user.baa_accepted_at,
        # When the user was shown (and answered) the optional quality-review
        # consent step during onboarding. Lets the wizard avoid re-prompting
        # once answered, independent of the opt-in flags themselves.
        "quality_review_consent_prompted_at": user.quality_review_consent_prompted_at,
        "profile_basics_completed_at": user.profile_basics_completed_at,
    }

    settings = get_settings()

    if settings.multi_tenancy_enabled:
        from ..auth.service import _resolve_practice_from_email

        practice = _resolve_practice_from_email(user.email)
        if practice:
            result["practice_id"] = practice[0]

    # Include subscription/trial info when subscription enforcement is enabled.
    if settings.is_saas:
        from .subscription import _get_subscription_info  # type: ignore[import-not-found]

        sub_info = _get_subscription_info(user.email, settings)
        if sub_info:
            result["subscription"] = sub_info

    return result


@router.post("/me/mfa-enrolled")
def record_mfa_enrollment(
    http_request: Request,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    identity_repo: IdentityRepository = Depends(get_identity_repository),
    audit: AuditService = Depends(get_audit_service),
) -> dict[str, str]:
    """
    Record that the user has completed MFA enrollment.

    Called by the frontend after successful TOTP enrollment via Firebase.
    Verifies enrollment server-side against the Firebase Admin SDK — the
    client's claim is not sufficient on its own, or an attacker could
    mint a bogus ``mfa_enrolled_at`` timestamp and poison compliance
    metrics.
    """
    # Post-indirection, user.id is the Pablo-internal id, not the Firebase
    # uid. Resolve via user_identities so a fresh self-serve signup
    # (Pablo id = fresh uuid4) can still be looked up against Firebase.
    firebase_uid = identity_repo.get_subject_id(user.id, "firebase")
    if firebase_uid is None:
        raise ServerError(
            "Authenticated user has no Firebase identity mapping",
            code="IDENTITY_MAPPING_MISSING",
        )

    try:
        has_totp = _user_has_totp_factor(firebase_uid)
    except LookupError as exc:
        # Identity Toolkit said "no such user" — authenticated user that
        # Firebase doesn't know about is a server invariant violation.
        logger.error("Identity Toolkit lookup miss for uid=%s", firebase_uid)
        raise ServerError(
            "Firebase did not recognize the authenticated user",
            code="FIREBASE_USER_LOOKUP_MISS",
        ) from exc
    except httpx.HTTPError as exc:
        logger.exception("Identity Toolkit accounts:lookup failed for uid=%s", firebase_uid)
        raise ServerError(
            "Failed to verify MFA enrollment with Firebase",
            code="MFA_VERIFICATION_FAILED",
        ) from exc

    if not has_totp:
        raise BadRequestError(
            "No TOTP factor enrolled for this user in Firebase",
            code="MFA_NOT_ENROLLED",
        )

    user.mfa_enrolled_at = utc_now()
    user_repo.update(user)
    audit.log_onboarding_milestone(
        AuditAction.ONBOARDING_MFA_ENROLLED, user, http_request
    )
    return {"mfa_enrolled_at": utc_now_iso()}


@router.get("/me")
def get_current_user_profile(
    user: User = Depends(get_current_user),
) -> User:
    """
    Get current user profile.

    Returns the authenticated user's profile information.
    """
    return user


@router.patch("/me")
def update_current_user_profile(
    http_request: Request,
    request: UpdateUserRequest,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    profile_repo: ClinicianProfileRepository = Depends(get_clinician_profile_repository),
    audit: AuditService = Depends(get_audit_service),
) -> User:
    """Partial update of the current user's profile.

    Posture: pre-MFA onboarding (route_security.py #2). This is the
    endpoint the onboarding wizard PATCHes for ``provider_type``,
    ``onboarding_state``, ``title``, and ``credentials``, all of which
    run BEFORE the user has completed MFA enrollment / re-sign-in.
    Same reasoning as the BAA endpoints: an authenticated Firebase
    token is required, but the second-factor claim isn't a meaningful
    gate for setting your own profile fields. PHI routes downstream
    remain MFA-required via ``require_mfa`` and
    ``require_baa_acceptance``.

    ``name``, ``provider_type``, and ``onboarding_state`` persist on
    the platform user row. ``title`` and ``credentials`` are
    per-practice profile metadata and persist on the tenant-scoped
    ``ClinicianProfile`` row — the active search_path is set by
    :class:`DatabaseSessionMiddleware` from the auth token, so the
    upsert lands in the correct schema.
    """
    if request.name is not None:
        user.name = request.name
    if request.legal_name is not None:
        user.legal_name = request.legal_name
    if request.provider_type is not None:
        user.provider_type = request.provider_type
    if request.onboarding_state is not None:
        prev_state = user.onboarding_state
        user.onboarding_state = request.onboarding_state
    if request.phone is not None:
        # Validator already stripped/normalized; blanks arrive as None and
        # leave the existing value untouched (PATCH semantics).
        user.phone = request.phone
    if request.profile_basics_completed:
        user.profile_basics_completed_at = utc_now()
    user_repo.update(user)

    if request.onboarding_state is not None:
        state_to_action = {
            "in_progress": AuditAction.ONBOARDING_STARTED,
            "completed": AuditAction.ONBOARDING_COMPLETED,
        }
        action = state_to_action.get(request.onboarding_state)
        if action is not None and prev_state != request.onboarding_state:
            audit.log_onboarding_milestone(
                action, user, http_request,
                changes={"previous": prev_state, "new": request.onboarding_state},
            )

    if request.title is not None or request.credentials is not None:
        _upsert_clinician_profile(
            profile_repo,
            user=user,
            title=request.title,
            credentials=request.credentials,
        )
        # Mirror onto the response so the client sees the persisted
        # state without a follow-up GET.
        if request.title is not None:
            user.title = request.title
        if request.credentials is not None:
            user.credentials = request.credentials

    return user


def _upsert_clinician_profile(
    profile_repo: ClinicianProfileRepository,
    *,
    user: User,
    title: str | None = None,
    credentials: str | None = None,
    license_number: str | None = None,
    license_state: str | None = None,
) -> None:
    """Upsert profile metadata on the caller's ClinicianProfile row.

    Handles title/credentials (set at profile-basics) and license_*
    (set at the professional-info step). Existing fields are preserved
    when the corresponding value is None — PATCH semantics, not PUT.
    Practice_id is resolved from the caller's email; the row's existing
    practice_id wins if a profile already exists, so this is a no-op for
    unmapped emails that have an existing row.
    """
    existing = profile_repo.get(user.id)
    practice_id = existing.practice_id if existing else _resolve_practice_id_for(user)
    if practice_id is None:
        # No practice mapping yet — the onboarding flow normally
        # provisions the practice before reaching this step. Skip the
        # write rather than failing the whole PATCH; other fields
        # (name, provider_type, onboarding_state) still persist.
        logger.warning(
            "Skipping clinician profile upsert for user_id=%s: no practice mapping",
            user.id,
        )
        return

    profile = ClinicianProfile(
        user_id=user.id,
        practice_id=practice_id,
        title=title if title is not None else (existing.title if existing else None),
        credentials=(
            credentials if credentials is not None else (existing.credentials if existing else None)
        ),
        role=existing.role if existing else "clinician",
        joined_at=existing.joined_at if existing else None,
        license_number=(
            license_number
            if license_number is not None
            else (existing.license_number if existing else None)
        ),
        license_state=(
            license_state
            if license_state is not None
            else (existing.license_state if existing else None)
        ),
    )
    # The RLS app.current_user_id GUC is armed centrally in _resolve_user
    # for every authenticated request (pre-MFA included), so the
    # clinician_profiles WITH CHECK policy is satisfied here without a
    # per-call arm.
    profile_repo.update(profile)


def _resolve_practice_id_for(user: User) -> str | None:
    """Resolve the practice_id for ``user`` from the platform email mapping."""
    from ..auth.service import _resolve_practice_from_email

    practice = _resolve_practice_from_email(user.email)
    return practice[0] if practice else None


@router.patch("/me/professional-info")
def update_professional_info(
    request: UpdateProfessionalInfoRequest,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    profile_repo: ClinicianProfileRepository = Depends(get_clinician_profile_repository),
) -> dict:
    """Persist professional credentials collected at the onboarding
    professional-info step.

    Splits the fields by their natural owner:
    - ``legal_name`` → platform user row (a person attribute).
    - ``license_number`` / ``license_state`` → tenant clinician profile
      (professional credentials, alongside title/credentials).
    - ``business_address`` → practice row (the covered entity's address,
      reused to build the BAA snapshot at acceptance time).

    Posture: pre-MFA onboarding (route_security.py #2), same as the
    other onboarding-gate writes. PATCH semantics — a None field leaves
    the stored value untouched.
    """
    if request.legal_name is not None:
        user.legal_name = request.legal_name
        user_repo.update(user)

    if request.license_number is not None or request.license_state is not None:
        _upsert_clinician_profile(
            profile_repo,
            user=user,
            license_number=request.license_number,
            license_state=request.license_state,
        )

    if request.business_address is not None:
        practice_id = _resolve_practice_id_for(user)
        if practice_id is not None:
            from ..db import get_db_session
            from ..db.platform_models import PracticeRow

            session = get_db_session()
            practice = session.get(PracticeRow, practice_id)
            if practice is not None:
                practice.address = request.business_address
                session.flush()
        else:
            logger.warning(
                "Skipping practice address write for user_id=%s: no practice mapping",
                user.id,
            )

    return {
        "legal_name": user.legal_name,
        "license_number": request.license_number,
        "license_state": request.license_state,
        "business_address": request.business_address,
    }


@router.get("/me/baa-status")
def get_baa_status(
    user: User = Depends(get_current_user_no_mfa),
    current_version: str = Depends(get_baa_version),
) -> BAAStatusResponse:
    """
    Get BAA acceptance status for the current user.

    Posture: pre-MFA onboarding (route_security.py #2). BAA acceptance
    runs in the same chicken-and-egg space as MFA enrollment — both
    are gates a user must satisfy before they can access PHI, and the
    user might land here with a token that doesn't yet have the MFA
    second-factor claim (e.g. fresh sign-in path before TOTP prompt,
    or session-cookie token from before enrollment refresh propagated).
    MFA is enforced separately by ``require_baa_acceptance`` on actual
    PHI routes downstream.

    Returns:
        - accepted: Whether user has accepted any version of BAA
        - accepted_at: Timestamp of acceptance (if accepted)
        - version: Version they accepted (if accepted)
        - current_version: The current BAA version
    """
    return BAAStatusResponse(
        accepted=user.baa_accepted_at is not None,
        accepted_at=user.baa_accepted_at,
        version=user.baa_version,
        current_version=current_version,
    )


@router.post("/me/accept-baa")
def accept_baa(
    http_request: Request,
    request: AcceptBAARequest,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    profile_repo: ClinicianProfileRepository = Depends(get_clinician_profile_repository),
    audit: AuditService = Depends(get_audit_service),
) -> BAAStatusResponse:
    """
    Accept the Business Associate Agreement.

    Posture: pre-MFA onboarding (route_security.py #2). Same reasoning
    as ``get_baa_status``: BAA acceptance is an onboarding gate that
    a user must clear before reaching PHI routes, and the token used
    to POST here may not yet carry the MFA second-factor claim. PHI
    routes downstream remain MFA-required via ``require_mfa``, and
    cannot be reached until ``baa_accepted_at`` is set anyway via
    ``require_baa_acceptance``.

    The BAA is between Pablo and the *covered entity* (the practice),
    not the individual clinician. The legal snapshot (signer name,
    license, address, full text) is therefore written onto the practice
    row. The credentials are read from the professional-info already
    collected earlier in onboarding — ``legal_name`` from the user row,
    ``license_*`` from the tenant clinician profile, ``name`` /
    ``address`` from the practice row — not re-submitted here.

    ``baa_accepted_at`` + ``baa_version`` are ALSO stamped on the user
    row so ``require_baa_acceptance`` can gate every PHI request without
    a per-request practice lookup.
    """
    if not request.accepted:
        raise BadRequestError("BAA must be accepted")

    # Load the full BAA text for the audit-trail snapshot.
    baa_path = _resolve_baa_path(request.version)
    baa_full_text = baa_path.read_text()

    now = utc_now()

    # Snapshot onto the practice row (the covered entity). PracticeRow is
    # schema-qualified to ``platform`` so it writes through the same
    # request-scoped session as the user update — one transaction, one
    # commit at request end.
    profile = profile_repo.get(user.id)
    practice_id = _resolve_practice_id_for(user)
    if practice_id is not None:
        from ..db import get_db_session
        from ..db.platform_models import PracticeRow

        session = get_db_session()
        practice = session.get(PracticeRow, practice_id)
        if practice is not None:
            practice.baa_accepted_at = now
            practice.baa_version = request.version
            practice.baa_legal_name = user.legal_name
            practice.baa_license_number = profile.license_number if profile else None
            practice.baa_license_state = profile.license_state if profile else None
            practice.baa_practice_name = practice.name
            practice.baa_business_address = practice.address
            practice.baa_full_text = baa_full_text
            session.flush()

    # Stamp the fast-path gate fields on the user row regardless of
    # whether a practice exists (self-hosted single-tenant has none).
    user.baa_accepted_at = now
    user.baa_version = request.version
    user_repo.update(user)
    audit.log_onboarding_milestone(
        AuditAction.ONBOARDING_BAA_ACCEPTED,
        user,
        http_request,
        changes={"version": request.version},
    )

    return BAAStatusResponse(
        accepted=True,
        accepted_at=now,
        version=request.version,
        current_version=request.version,
    )


@router.get("/me/security-guide-status")
def get_security_guide_status(
    user: User = Depends(get_current_user_no_mfa),
) -> SecurityGuideStatusResponse:
    """Return security-guide acknowledgment status for the current user.

    The "current version" is declared by the frontend (the guide
    document is shipped with the client), so this endpoint only
    reports what the user has acknowledged. The frontend compares
    against its bundled version to decide whether to re-prompt.
    """
    return SecurityGuideStatusResponse(
        acknowledged=user.security_guide_acknowledged_at is not None,
        acknowledged_at=user.security_guide_acknowledged_at,
        version=user.security_guide_version,
    )


@router.post("/me/acknowledge-security-guide")
def acknowledge_security_guide(
    http_request: Request,
    request: AcknowledgeSecurityGuideRequest,
    user: User = Depends(get_current_user_no_mfa),
    user_repo: UserRepository = Depends(get_user_repository),
    audit: AuditService = Depends(get_audit_service),
) -> SecurityGuideStatusResponse:
    """Record acknowledgment of the security & privacy guide.

    Idempotent — calling again with the same or a different version
    overwrites both fields and re-emits the audit event so the
    acknowledgment history is complete.
    """
    user.security_guide_acknowledged_at = utc_now()
    user.security_guide_version = request.version
    user_repo.update(user)
    audit.log_onboarding_milestone(
        AuditAction.ONBOARDING_SECURITY_GUIDE_ACKNOWLEDGED,
        user,
        http_request,
        changes={"version": request.version},
    )
    return SecurityGuideStatusResponse(
        acknowledged=True,
        acknowledged_at=user.security_guide_acknowledged_at,
        version=request.version,
    )


@router.get("/baa/{version}", response_class=PlainTextResponse)
def get_baa_text(
    version: str,
    _user: User = Depends(get_current_user_no_mfa),
) -> str:
    """
    Get the full text of a specific BAA version.

    Posture: pre-MFA onboarding (route_security.py #2). The
    ``/baa-acceptance`` page fetches this in the same render cycle as
    ``/me/baa-status``; if either requires MFA the BAA flow becomes
    unreachable for any signup flow that hits BAA before completing
    MFA sign-in. The response is the BAA markdown — public-equivalent
    content; authentication is kept only so the response is per-user
    auditable, not because the content is sensitive.

    This endpoint serves the Business Associate Agreement text in markdown format.

    Args:
        version: The BAA version identifier (e.g., "2024-01-01")

    Returns:
        The full BAA text in markdown format
    """
    baa_path = _resolve_baa_path(version)
    return baa_path.read_text()


@router.get("/baa", response_class=PlainTextResponse)
def get_current_baa(
    current_version: str = Depends(get_baa_version),
    _public: None = Depends(truly_public),
) -> str:
    """
    Get the current BAA version text.

    Returns the most recent Business Associate Agreement text in markdown format.
    """
    return get_baa_text(current_version)


@router.get("/me/preferences")
def get_preferences(
    user: User = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserPreferences:
    """Fetch user preferences. Returns defaults if never saved."""
    return user_repo.get_preferences(user.id)


@router.put("/me/preferences")
def save_preferences(
    prefs: UserPreferences,
    user: User = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserPreferences:
    """Save user preferences (full replace)."""
    return user_repo.save_preferences(user.id, prefs)


@router.put("/me/preferences/theme")
def save_theme_preference(
    request: UpdateThemeRequest,
    user: User = Depends(get_current_user),
    user_repo: UserRepository = Depends(get_user_repository),
) -> UserPreferences:
    """Update just the UI theme without round-tripping the full prefs blob."""
    prefs = user_repo.get_preferences(user.id)
    prefs.theme = request.theme
    return user_repo.save_preferences(user.id, prefs)


class AuditLogItem(BaseModel):
    # Omits user_id (implicit), changes (PHI-adjacent), and expires_at.
    id: str
    timestamp: str
    action: str
    resource_type: str
    resource_id: str
    patient_id: str | None = None
    session_id: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None


class AuditLogResponse(BaseModel):
    data: list[AuditLogItem]
    limit: int


AUDIT_LOG_MAX_LIMIT = 500


@router.get("/me/audit-log", response_model=AuditLogResponse)
def list_my_audit_log(
    request: Request,
    since: datetime | None = Query(
        None, description="Return rows strictly after this ISO-8601 timestamp."
    ),
    limit: int = Query(100, ge=1, le=AUDIT_LOG_MAX_LIMIT),
    user: User = Depends(get_current_user),
    _ctx: TenantContext = Depends(get_tenant_context),
    audit: AuditService = Depends(get_audit_service),
) -> AuditLogResponse:
    """Return the caller's own audit rows, newest first."""
    entries = audit.list_for_user(user_id=user.id, since=since, limit=limit)
    audit.log_self_audit_view(user=user, request=request, returned_count=len(entries))
    return AuditLogResponse(
        data=[
            AuditLogItem(
                id=e.id,
                timestamp=e.timestamp,
                action=e.action,
                resource_type=e.resource_type,
                resource_id=e.resource_id,
                patient_id=e.patient_id,
                session_id=e.session_id,
                ip_address=e.ip_address,
                user_agent=e.user_agent,
            )
            for e in entries
        ],
        limit=limit,
    )
