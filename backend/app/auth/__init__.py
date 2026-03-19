"""Authentication module for Firebase/Identity Platform token validation."""

from .service import (
    TenantContext,
    extract_tenant_id,
    get_current_user_id,
    get_tenant_context,
    require_mfa,
    resolve_tenant_database,
)

__all__ = [
    "TenantContext",
    "extract_tenant_id",
    "get_current_user_id",
    "get_tenant_context",
    "require_mfa",
    "resolve_tenant_database",
]
