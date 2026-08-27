"""Authentication module for Firebase token validation."""

from .patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientPrincipalResolver,
    PatientResolverRegistry,
    get_patient_context,
    get_patient_resolver_registry,
    patient_resolver_registry,
)
from .route_security import truly_public
from .service import (
    TenantContext,
    get_current_user_id,
    get_tenant_context,
    require_mfa,
)

__all__ = [
    "AuthStrength",
    "PatientContext",
    "PatientCredential",
    "PatientPrincipalResolver",
    "PatientResolverRegistry",
    "TenantContext",
    "get_current_user_id",
    "get_patient_context",
    "get_patient_resolver_registry",
    "get_tenant_context",
    "patient_resolver_registry",
    "require_mfa",
    "truly_public",
]
