# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Application settings and configuration management.

Uses pydantic-settings for type-safe environment variable handling.
Configuration can be loaded from .env files (local dev) or environment variables (production).

HIPAA Compliance: Manages security settings including TLS enforcement
and environment-specific configurations for PHI protection.
"""

import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Matches -prod, -production, -prod<N> at end of project id. The
# leading `-` prevents substring traps (reproduction, approved).
_PROD_PROJECT_PATTERN = re.compile(r"-prod(?:uction)?\d*$")


class Settings(BaseSettings):
    """
    Application configuration settings.

    Loads from environment variables with fallback to .env file for local development.
    All sensitive values (secrets, passwords) use SecretStr to prevent accidental logging.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application Settings
    debug: bool = Field(default=False, description="Enable debug mode")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO",
        description="Logging level",
    )
    # Environment defaults to production for security (fail-safe default)
    # Must explicitly set ENVIRONMENT=development for local development
    environment: Literal["development", "staging", "production"] = Field(
        default="production",
        description="Deployment environment",
    )

    # Security Settings - HIPAA TLS Requirements
    # HTTPS is automatically enforced in production/staging, disabled in development
    # 2 years (63072000s) matches the privacy policy commitment and the
    # IETF + browser-vendor recommendation for HSTS-preloaded production sites.
    hsts_max_age: int = Field(
        default=63072000,
        description="HSTS max-age directive in seconds (production only)",
    )
    hsts_include_subdomains: bool = Field(
        default=True,
        description="Include subdomains in HSTS policy",
    )
    hsts_preload: bool = Field(
        default=True,
        description="Enable HSTS preload",
    )

    # BAA (Business Associate Agreement) Settings
    require_baa: bool = Field(
        default=False,
        description=(
            "Require BAA acceptance before PHI access. Hosted "
            "deployments set this to True via setup.sh. Self-hosted "
            "defaults to False — the operator signs a BAA directly "
            "with their cloud provider."
        ),
    )

    # MFA (Multi-Factor Authentication) Settings
    require_mfa: bool = Field(
        default=True,
        description=(
            "Require TOTP MFA for all users. "
            "HIPAA §164.312(d) requires strong authentication. "
            "Set to False only for local development."
        ),
    )
    e2e_test_emails_raw: str = Field(
        default="",
        alias="E2E_TEST_EMAILS",
        description=(
            "Comma-separated emails that bypass MFA for E2E testing. "
            "Only honored in non-production environments. "
            "Example: E2E_TEST_EMAILS=test@pablo.health"
        ),
    )

    # Server-enforced session idle timeout. The frontend IdleTimeout
    # component handles the warning dialog and proactive logout for
    # active tabs; this setting is the backend safety net that catches
    # stale sessions (tabs restored from bfcache, Safari relaunches,
    # tampered/replayed tokens). Must stay in sync with the IDLE_TIMEOUT_MS
    # constant in frontend/src/components/IdleTimeout.tsx.
    idle_timeout_seconds: int = Field(
        default=15 * 60,
        description=(
            "Maximum allowed gap between authenticated requests before "
            "the session is forcibly expired (HIPAA §164.312(a)(2)(iii)). "
            "Requires Redis; no-op when USE_REDIS=false."
        ),
    )
    idle_session_fail_open: bool = Field(
        default=False,
        description=(
            "Behaviour when the idle-session Redis check raises an error "
            "(transient outage). False (default, HIPAA-safe): fail closed — "
            "reject the request with 503 so the auto-logoff control is never "
            "silently disabled. True: allow the request through (availability "
            "over strict enforcement). Note: this governs the *error* path "
            "only; when Redis is intentionally disabled (USE_REDIS=false) the "
            "check is a no-op regardless."
        ),
    )

    # HIPAA Audit Logging — defense-in-depth dual-write
    audit_dual_write_enabled: bool = Field(
        default=True,
        description=(
            "Dual-write every AuditService event to GCP Cloud Logging "
            "under logName='pablo.audit_events'. A retention-locked GCS "
            "sink mirrors these for tamper-evident HIPAA retention "
            "(§ 164.312(c)(2) integrity protection). Best-effort: Cloud "
            "Logging failures log a warning but do NOT fail the request. "
            "Disable for environments without GCP credentials "
            "(local dev, CI) via AUDIT_DUAL_WRITE_ENABLED=false."
        ),
    )
    audit_read_coalesce_seconds: int = Field(
        default=0,
        description=(
            "Coalesce duplicate read-access audit events (patient/session/"
            "chat-conversation/document views) for the same (user, record) "
            "within this many seconds. Continuous work on one chart otherwise "
            "writes one row per HTTP GET, including frontend refetches. "
            "0 (default) disables coalescing, so deployments without Redis "
            "keep one-row-per-read behavior unchanged. Requires USE_REDIS=true "
            "to take effect; restricted-category document views are never "
            "coalesced and always recorded at full fidelity."
        ),
    )

    internal_actor_user_ids_raw: str = Field(
        default="",
        alias="INTERNAL_ACTOR_USER_IDS",
        description=(
            "Comma-separated user IDs for authorized automated actors "
            "(scheduled internal scans, test/E2E identities). The audit-log "
            "review annotates entries from these users so its anomaly model "
            "attributes their machine-paced traffic rather than treating it "
            "as a snooping signal. Empty (default): every actor is judged on "
            "behaviour alone. Configure per-deployment."
        ),
    )

    @property
    def e2e_test_emails(self) -> set[str]:
        """Parse comma-separated E2E_TEST_EMAILS into a set."""
        if not self.e2e_test_emails_raw:
            return set()
        return {e.strip() for e in self.e2e_test_emails_raw.split(",") if e.strip()}

    @property
    def internal_actor_user_ids(self) -> set[str]:
        """Parse comma-separated INTERNAL_ACTOR_USER_IDS into a set."""
        if not self.internal_actor_user_ids_raw:
            return set()
        return {u.strip() for u in self.internal_actor_user_ids_raw.split(",") if u.strip()}

    # Trusted Proxy Settings
    trusted_proxy_ips: str = Field(
        default="",
        description=(
            "Trusted proxy IPs for X-Forwarded-Proto/X-Forwarded-SSL headers. "
            "Empty string (default): trust no proxies (secure default). "
            "'*': trust all proxies (use for Cloud Run/GKE). "
            "Comma-separated IPs: trust specific proxies."
        ),
    )
    trusted_proxy_hops: int = Field(
        default=1,
        ge=1,
        description=(
            "Number of trusted reverse-proxy hops in front of the app, used "
            "to pick the real client IP out of X-Forwarded-For. The client "
            "IP is read this many entries from the RIGHT (the proxy-appended "
            "end), never the leftmost (client-spoofable) entry. Cloud Run "
            "directly = 1 (GFE appends the real client IP last); add 1 per "
            "extra trusted proxy (e.g. an external HTTP(S) load balancer)."
        ),
    )

    # Sign-up Restriction Settings
    restrict_signups: bool = Field(
        default=False,
        description=(
            "Only allowlisted emails can sign in. Hosted "
            "deployments set this to True via setup.sh. Self-hosted "
            "defaults to False — a solo operator doesn't need an "
            "allowlist."
        ),
    )

    # API Settings
    api_title: str = Field(
        default="Pablo API",
        description="API title",
    )
    api_description: str = Field(
        default="Backend API for therapy session management and SOAP note generation",
        description="API description",
    )

    # Stripe Settings (used by the optional billing overlay)
    stripe_secret_key: SecretStr = Field(
        default=SecretStr(""),
        description="Stripe API secret key for billing portal session creation",
    )
    app_url: str = Field(
        default="http://localhost:3000",
        description="Frontend app URL (used as return_url for Stripe portal)",
    )
    companion_launch_url: str = Field(
        default="",
        description=(
            "Base URL for the companion handoff links the app hands out at "
            "POST /api/launch/intent. Falls back to app_url when unset, which "
            "is the right default for a single-host deployment.\n\n"
            "Set this to a DIFFERENT host than app_url when the handoff is "
            "started from a page the app itself serves. A browser treats a "
            "link to the host it is already on as ordinary navigation and "
            "follows it in the tab, so a same-host link never reaches the "
            "desktop app — it has to be a distinct hostname (for example a "
            "'launch.' subdomain) that also serves the association file at "
            "/.well-known/apple-app-site-association. Leave it empty unless "
            "that hostname exists and resolves; a host without DNS or a "
            "certificate yields a dead link."
        ),
    )
    dpop_trusted_hosts: str = Field(
        default="",
        description=(
            "Comma-separated extra public hosts the DPoP middleware may honor "
            "from X-Forwarded-Host when canonicalizing the request URL. The "
            "hosts of backend_base_url (the API's own public origin) and "
            "app_url are always trusted; this is for deployments that serve "
            "the API under additional public hostnames (e.g. a custom domain "
            "plus the run.app URL). Untrusted forwarded hosts are ignored — "
            "the raw request host is used instead.\n\n"
            "IMPORTANT: the companion signs DPoP proofs against the API host "
            "it talks to directly, so that host MUST appear in the trusted "
            "set, otherwise every enrolled-companion request 401s once "
            "ENABLE_DPOP_VALIDATION is on. It is covered automatically when "
            "backend_base_url or app_url already names it; list it here only "
            "when the public API host is neither of those."
        ),
    )

    # CORS Settings
    cors_origins: str = Field(
        default="http://localhost:3000",
        description="Allowed CORS origin",
    )
    cors_allow_credentials: bool = Field(
        default=True,
        description="Allow credentials in CORS requests",
    )

    # Multi-Tenancy Settings
    multi_tenancy_enabled: bool = Field(
        default=False,
        description=(
            "Enable per-practice multi-tenancy. When enabled, requests are "
            "routed to per-practice PostgreSQL schemas, resolved from the "
            "authenticated user's email via the platform.email_tenant_mappings "
            "table (not from a token claim)."
        ),
    )

    # Pentest runner (Google service-account OIDC auth)
    pentest_runner_sa_email: str = Field(
        default="",
        description=(
            "Service-account email the pentest runner uses. "
            "OIDC tokens whose `email` claim matches this value are "
            "accepted by /api/admin/pentest/* endpoints. Empty = no runner "
            "configured (endpoints return 503)."
        ),
    )
    pentest_runner_audience: str = Field(
        default="",
        description=(
            "Expected `aud` claim on pentest-runner OIDC tokens. "
            "Must equal the URL the runner passes to "
            "`--audiences` when minting its ID token (typically the "
            "Cloud Run service URL)."
        ),
    )

    # Pablo Edition (feature gating)
    pablo_edition: Literal["core", "solo", "practice"] = Field(
        default="core",
        description=(
            "Pablo edition controls feature availability. "
            "'core' = self-hosted open-source (Pablo Core). "
            "'solo' = Pablo Solo hosted ($19-24/mo). "
            "'practice' = Pablo Practice multi-therapist."
        ),
    )

    # Pluggable OIDC auth backend (additive).
    # When oidc_issuer is non-empty the backend will additionally accept
    # ID tokens from this issuer, dispatched on the token's `iss` claim and
    # resolved through the same user_identities mapping as Firebase. All
    # three empty (the default) means Firebase-only, identical behavior.
    oidc_issuer: str = Field(
        default="",
        description=(
            "OIDC issuer URL (the `iss` claim) of an additional accepted "
            "token issuer, e.g. https://keycloak.example.com/realms/pablo. "
            "Empty disables the OIDC backend (Firebase-only)."
        ),
    )
    oidc_audience: str = Field(
        default="",
        description=("Expected `aud` claim on OIDC ID tokens. Required when oidc_issuer is set."),
    )
    oidc_jwks_uri: str = Field(
        default="",
        description=(
            "JWKS endpoint URL for the OIDC issuer's RS256 signing keys, "
            "e.g. https://keycloak.example.com/realms/pablo/protocol/"
            "openid-connect/certs. Required when oidc_issuer is set."
        ),
    )

    # WebAuthn / passkey authentication (PABLO-egm).
    # The relying-party id is the registrable domain a passkey is scoped to;
    # origins are the full https:// values the assertion's clientDataJSON must
    # match. Defaults target local dev; deployments set them via env. The RP id
    # must be a registrable suffix of every configured origin.
    webauthn_rp_id: str = Field(
        default="localhost",
        description=(
            "WebAuthn Relying Party ID — the registrable domain passkeys are "
            "scoped to (e.g. pablo.health). Must be a registrable suffix of "
            "every origin in WEBAUTHN_ORIGINS."
        ),
    )
    webauthn_rp_name: str = Field(
        default="Pablo",
        description="Relying Party display name shown in the OS passkey prompt.",
    )
    webauthn_attestation: str = Field(
        default="none",
        description=(
            "WebAuthn attestation conveyance requested at registration: one of "
            "none|indirect|direct|enterprise. 'none' (the default) asks for no "
            "attestation; 'direct' requests the authenticator's attestation so "
            "the RP can verify its provenance (e.g. genuine Apple/Microsoft)."
        ),
    )
    webauthn_attestation_roots_dir: str = Field(
        default="",
        description=(
            "Directory of curated attestation root-CA certificates used to "
            "verify authenticator provenance. Files are named "
            "'<fmt>.pem' (e.g. 'apple.pem', 'packed.pem', 'fido-u2f.pem', "
            "'tpm.pem'); each may concatenate multiple PEM roots for that "
            "attestation format. Empty (the default) disables chain "
            "verification — credentials still enroll, but attestation_verified "
            "is always false (informational only)."
        ),
    )
    webauthn_attestation_require_trusted_root: bool = Field(
        default=False,
        description=(
            "Strict mode: reject registration when the authenticator presents "
            "an attestation of a format we have roots for but its certificate "
            "chain does not validate to a trusted root. Default false accepts "
            "the enrolment and records attestation_verified=false. Requires "
            "WEBAUTHN_ATTESTATION_ROOTS_DIR to be set to have any effect."
        ),
    )
    webauthn_admin_require_hardware_key: bool = Field(
        default=False,
        description=(
            "When true, platform-admin routes require step-up with a "
            "device-bound (hardware) passkey — a synced passkey or TOTP does "
            "not satisfy admin access. Default false leaves admin auth "
            "unchanged. Enable only after admins hold >=2 hardware keys "
            "(anti-lockout) and attestation roots are provisioned."
        ),
    )
    webauthn_origins_raw: str = Field(
        default="http://localhost:3000",
        alias="WEBAUTHN_ORIGINS",
        description=(
            "Comma-separated allowed origins (full scheme+host[:port] values) a "
            "passkey ceremony may originate from, e.g. "
            "https://app.pablo.health,https://dev.pablo.health."
        ),
    )

    @property
    def webauthn_origins(self) -> list[str]:
        """Parse comma-separated WEBAUTHN_ORIGINS into a list of origins."""
        return [o.strip() for o in self.webauthn_origins_raw.split(",") if o.strip()]

    @model_validator(mode="after")
    def _validate_oidc_config(self) -> "Settings":
        """Fail fast if the OIDC backend is half-configured.

        When ``oidc_issuer`` is set, the audience and JWKS URI must be too —
        otherwise ``OidcVerifier`` would be built with an empty audience and
        rely on PyJWT's empty-aud handling as an implicit backstop. We refuse
        that at startup instead. (http issuers are intentionally allowed so a
        local Keycloak spike on http://localhost works; production uses https.)
        """
        if self.oidc_issuer:
            missing = [
                name
                for name, value in (
                    ("oidc_audience", self.oidc_audience),
                    ("oidc_jwks_uri", self.oidc_jwks_uri),
                )
                if not value
            ]
            if missing:
                raise ValueError(
                    f"oidc_issuer is set but {', '.join(missing)} must also be "
                    "set to enable the OIDC auth backend"
                )
        return self

    # Firebase Blocking Function OIDC Verification
    # The blocking functions (beforeCreate / beforeSignIn) call this backend
    # with a Google-signed OIDC token. We verify audience + issuer + caller
    # email to ensure only the configured blocking function SA can reach the
    # /api/ext/auth endpoints.
    backend_base_url: str = Field(
        default="",
        description=(
            "This service's public base URL. Used as the expected audience "
            "for OIDC tokens from Firebase blocking functions. "
            "Example: https://pablo-backend-xxx-uc.a.run.app. "
            "If empty, audience is not enforced (logged as a warning on startup)."
        ),
    )
    blocking_function_service_account: str = Field(
        default="",
        description=(
            "Service account email of the Firebase blocking function runtime. "
            "When set, only tokens minted by this SA are accepted on "
            "/api/ext/auth endpoints. If empty, caller identity is not "
            "enforced (logged as a warning on startup)."
        ),
    )

    database_url: str = Field(
        default="",
        description=("PostgreSQL connection URL. Format: postgresql://user:pass@host:port/dbname"),
    )
    database_pool_size: int = Field(
        default=5,
        description=(
            "SQLAlchemy QueuePool ``pool_size`` -- warm connections kept in the pool per "
            "app instance. Per-instance * instance count must stay under the database's "
            "``max_connections`` ceiling minus headroom for migrations/admin. Conservative "
            "default sized for a self-hosted Postgres with ``max_connections=25``; managed "
            "deployments should set this via env var to match the actual tier."
        ),
    )
    database_max_overflow: int = Field(
        default=10,
        description=(
            "SQLAlchemy QueuePool ``max_overflow`` -- extra connections briefly opened "
            "above ``pool_size`` under burst. Counts against the same database "
            "``max_connections`` budget as ``pool_size``."
        ),
    )
    database_pool_recycle_seconds: int = Field(
        default=1800,
        description=(
            "SQLAlchemy QueuePool ``pool_recycle`` -- proactively close and reopen a "
            "pooled connection after this many seconds, so the pool self-heals before "
            "the server (or a network path) drops an idle connection. Guards against "
            "``SSL connection has been closed unexpectedly`` on long-lived pools. "
            "``pool_pre_ping`` still catches connections that died earlier than this; "
            "set to ``-1`` to disable recycling."
        ),
    )
    database_connect_timeout_seconds: int = Field(
        default=5,
        description=(
            "libpq ``connect_timeout`` -- abort a connection attempt after this many "
            "seconds instead of hanging on an unreachable/slow server. Bounds the "
            "worst case when the pool has to open a NEW connection (cold start, or "
            "replacing a dropped one); without it a hung connect can stall a request "
            "for minutes. Keep it short since a healthy connect is sub-second."
        ),
    )
    database_tcp_keepalives_idle_seconds: int = Field(
        default=30,
        description=(
            "libpq TCP keepalive idle interval, in seconds. Sends keepalive probes on "
            "otherwise-idle connections so the network path (NAT/LB/firewall) does not "
            "silently drop them -- the usual cause of "
            "``SSL connection has been closed unexpectedly``. Set to ``0`` to disable "
            "keepalives entirely."
        ),
    )
    database_lock_timeout_ms: int = Field(
        default=5000,
        description=(
            "Per-connection ``lock_timeout`` GUC, in milliseconds. A statement waiting on "
            "a lock fails fast rather than stalling for the default deadlock-detection "
            "cycle. Set 0 to disable."
        ),
    )
    database_idle_in_transaction_timeout_ms: int = Field(
        default=30000,
        description=(
            "Per-connection ``idle_in_transaction_session_timeout`` GUC, in milliseconds. "
            "Kills connections that sit idle inside an open transaction (catches "
            "'transaction held across slow external call' regressions). Set 0 to disable."
        ),
    )
    database_statement_timeout_ms: int = Field(
        default=60000,
        description=(
            "Per-connection ``statement_timeout`` GUC, in milliseconds. Generous upper "
            "bound on any single query so runaway scans surface as failures. Set 0 to "
            "disable."
        ),
    )

    # Cloud SQL Python Connector (optional; off by default)
    db_use_cloud_sql_connector: bool = Field(
        default=False,
        description=(
            "When true, connect to PostgreSQL via the Cloud SQL Python connector instead of "
            "a plain TCP/Unix-socket DSN. Requires the ``cloud-sql-python-connector`` package "
            "(install the ``cloudsql`` extras group). Off by default; existing "
            "``DATABASE_URL``-based deployments are completely unaffected."
        ),
    )
    cloud_sql_instance_connection_name: str | None = Field(
        default=None,
        description=(
            "Cloud SQL instance connection name in the form ``PROJECT:REGION:INSTANCE``. "
            "Required when ``db_use_cloud_sql_connector=true``."
        ),
    )
    cloud_sql_ip_type: str = Field(
        default="PRIVATE",
        description=(
            "IP address type used by the Cloud SQL connector. One of ``PRIVATE``, ``PUBLIC``, "
            "or ``PSC``. Defaults to ``PRIVATE`` (VPC-internal, no Cloud SQL proxy needed). "
            "Only used when ``db_use_cloud_sql_connector=true``."
        ),
    )
    db_iam_auth: bool = Field(
        default=False,
        description=(
            "When true, use IAM database authentication instead of a password when connecting "
            "via the Cloud SQL Python connector. The connecting service account must have the "
            "``roles/cloudsql.instanceUser`` role and the database user must be created with "
            "``CREATE USER ... WITH TYPE 'CLOUD_IAM_SERVICE_ACCOUNT'``. Only used when "
            "``db_use_cloud_sql_connector=true``."
        ),
    )

    # Google Cloud
    gcp_project_id: str = Field(
        default="",
        description="GCP project ID",
    )

    # Firebase Authentication
    firebase_project_id: str = Field(
        default="",
        description="Firebase project ID for token verification (falls back to gcp_project_id)",
    )

    # Keyless Firebase Admin credentials for hosts without Application Default
    # Credentials (e.g. running on AWS, not GCP). When enabled, the Firebase
    # Admin SDK authenticates via Workload Identity Federation instead of ADC —
    # the runtime's cloud identity is federated to impersonate a service
    # account, so token verification AND custom-token signing (passkeys) work
    # without a static key. Default off; GCP deployments keep using ADC.
    firebase_workload_identity: bool = Field(
        default=False,
        description="Use Workload Identity Federation for Firebase Admin creds (non-GCP hosts)",
    )
    firebase_wif_audience: str = Field(
        default="",
        description="WIF provider audience (//iam.googleapis.com/projects/.../providers/...)",
    )
    firebase_wif_sa_impersonation_url: str = Field(
        default="",
        description="IAM Credentials generateAccessToken URL for the impersonated service account",
    )

    # Upload Settings
    max_upload_mb: int = Field(
        default=30,
        description="Maximum file upload size in megabytes",
    )

    # Redis Settings
    use_redis: bool = Field(
        default=False,
        description=(
            "Use Redis for shared state (auth codes, rate limiting, tenant cache). "
            "Required for multi-instance Cloud Run deployments. "
            "When False, uses in-memory stores (fine for single-instance / self-hosted)."
        ),
    )
    redis_host: str = Field(default="localhost", description="Redis host")
    redis_port: int = Field(default=6379, description="Redis port")
    redis_password: SecretStr = Field(
        default=SecretStr(""),
        description="Redis password",
    )
    redis_db: int = Field(default=0, description="Redis database number")
    redis_ssl: bool = Field(default=False, description="Use SSL for Redis connection")

    # Cache Settings
    cache_ttl_seconds: int = Field(
        default=300,
        description="Default cache TTL in seconds",
    )

    # Google Cloud Secret Manager (optional, for production)
    use_secret_manager: bool = Field(
        default=False,
        description="Load secrets from GCP Secret Manager instead of env vars",
    )

    # Transcription Service Settings
    transcription_enabled: bool = Field(
        default=False,
        description=(
            "Enable server-side audio transcription. "
            "When enabled, audio uploads are accepted and queued for processing "
            "via the configured transcription provider."
        ),
    )
    transcription_provider: Literal["whisper", "assemblyai"] = Field(
        default="assemblyai",
        description=(
            "Transcription provider for session audio. "
            "'assemblyai' = AssemblyAI batch API (requires a signed BAA and "
            "ASSEMBLYAI_API_KEY) — the supported, operational path and the "
            "default. "
            "'whisper' = self-hosted faster-whisper on GCP Batch spot GPUs; "
            "planned but not yet operational (the Batch worker image and queue "
            "are not wired), so it is not a working default."
        ),
    )
    transcription_audio_bucket: str = Field(
        default="pablo-audio",
        description=(
            "Bucket for encrypted audio uploads, on the provider selected by file_storage_provider."
        ),
    )
    transcription_audio_upload_url_ttl_seconds: int = Field(
        default=3600,
        description=(
            "V4 signed PUT URL lifetime for session-audio uploads. Sized for "
            "multi-hundred-MB two-channel raw session recordings on slow "
            "links (~864 MB observed for a 50-minute raw PCM session) — the "
            "300s document-upload TTL expired mid-upload in the 2026-07-16 "
            "e2e run. The PUT URL is still constrained to a single object "
            "name + content type + max-bytes ceiling at sign time, so the "
            "longer window only widens misuse of exactly one pre-named "
            "object, nothing else."
        ),
    )
    # Object-storage provider for file upload/download surfaces (patient
    # documents, signed-URL session audio, hard-purge blob deletes).
    # Bucket-name settings (patient_documents_gcs_bucket,
    # transcription_audio_bucket) name a bucket on whichever provider is
    # selected here.
    file_storage_provider: Literal["gcs", "s3"] = Field(
        default="gcs",
        description=(
            "Object-storage backend for file uploads/downloads. "
            "'gcs' = Google Cloud Storage (default, managed deployments). "
            "'s3' = AWS S3 or S3-compatible (MinIO/LocalStack); requires "
            "`poetry install --with aws` and boto3-discoverable credentials."
        ),
    )
    aws_region: str | None = Field(
        default=None,
        description=(
            "AWS region for the S3 file storage provider. Leave unset to "
            "use boto3's default resolution (AWS_DEFAULT_REGION, profile)."
        ),
    )
    aws_s3_endpoint_url: str | None = Field(
        default=None,
        description=(
            "Custom S3 endpoint for S3-compatible stores (MinIO, "
            "LocalStack). Leave unset for AWS S3."
        ),
    )
    # Compliance document storage (license copies, insurance declarations,
    # etc.). Accepts ``gs://<bucket>[/prefix]`` (GCS), ``s3://<bucket>[/prefix]``
    # (AWS S3 / S3-compatible), or an absolute local directory path (self-hosted
    # deployments; e.g. an EFS mount). When unset, the
    # /api/compliance/{id}/documents surface returns 503 with a clear
    # configuration message.
    compliance_documents_storage_root: str | None = Field(
        default=None,
        description=(
            "Storage root for clinician-uploaded compliance evidence documents. "
            "Use ``gs://<bucket>[/prefix]`` for GCS, ``s3://<bucket>[/prefix]`` "
            "for AWS S3, or an absolute local directory path (e.g. an EFS "
            "mount) for self-hosted deployments. Leave unset to disable the "
            "compliance-document upload surface."
        ),
    )
    # Maximum size for a single compliance document upload.
    compliance_documents_max_bytes: int = Field(
        default=25 * 1024 * 1024,
        description="Maximum compliance document upload size (bytes).",
    )

    # Patient document upload (THERAPY-ak6m.2). When unset, the
    # /api/patients/{id}/documents surface returns 503 with a clear
    # configuration message — keeps self-hosters who haven't provisioned
    # a bucket from quietly getting a broken upload flow.
    patient_documents_gcs_bucket: str | None = Field(
        default=None,
        description=(
            "Bucket for clinician-uploaded patient documents (PDFs, "
            "PNG/JPEG scans), on the provider selected by "
            "file_storage_provider. Per-tenant prefix: "
            "<bucket>/<tenant_id>/<uuid>. Leave unset to disable "
            "the patient_documents API surface."
        ),
    )
    # Signed-URL TTLs. Upload URL only needs to live for the browser's
    # PUT round-trip; download URL is consumed immediately by a 302
    # redirect. 5 min on both keeps the window of misuse small. The
    # PUT URL is additionally constrained to a single object name +
    # content type + 25 MB ceiling at sign time so leakage is
    # unforgeable.
    patient_documents_upload_url_ttl_seconds: int = Field(
        default=300,
        description="V4 signed PUT URL lifetime for patient-document uploads.",
    )
    patient_documents_download_url_ttl_seconds: int = Field(
        default=300,
        description="V4 signed GET URL lifetime for patient-document downloads.",
    )
    patient_documents_max_bytes: int = Field(
        default=25 * 1024 * 1024,
        description="Maximum patient-document upload size (bytes).",
    )

    # Email delivery for notification surfaces (booking confirmations,
    # reminders). 'none' logs and drops every message — the seam a
    # delivery-dependent caller must check via EmailSender.can_deliver
    # before it can refuse to arm.
    email_backend: Literal["none", "smtp"] = Field(
        default="none",
        description=(
            "Email backend for notification surfaces. "
            "'none' = log only, no delivery (default) — a bare deployment "
            "behaves exactly as it does today. "
            "'smtp' = deliver via SMTP with STARTTLS; requires smtp_host, "
            "smtp_port, smtp_username, smtp_password, smtp_from."
        ),
    )
    smtp_host: str = Field(default="", description="SMTP server hostname")
    smtp_port: int = Field(default=587, description="SMTP server port (587 for STARTTLS)")
    smtp_username: str = Field(default="", description="SMTP auth username")
    smtp_password: SecretStr = Field(default=SecretStr(""), description="SMTP auth password")
    smtp_from: str = Field(default="", description="From address for outbound email")

    # Document AI OCR fallback for scanned PDFs (THERAPY-ak6m.2.3).
    # Leave processor_id unset to disable — scanned PDFs land with
    # extracted_text=NULL, same as before the feature existed.
    document_ai_project_id: str | None = Field(
        default=None,
        description="GCP project that owns the Document AI OCR processor.",
    )
    document_ai_location: str = Field(
        default="us",
        description="Document AI processor location ('us' or 'eu').",
    )
    document_ai_processor_id: str | None = Field(
        default=None,
        description="Resource id of the OCR processor (hex suffix of the processor name).",
    )
    document_ai_max_pages: int = Field(
        default=30,
        description=(
            "Refuse OCR above this page count. Document AI's sync "
            "processDocument API rejects requests over ~30 pages."
        ),
    )
    allow_document_ai_ocr: bool = Field(
        default=True,
        description="Global kill-switch for the OCR fallback.",
    )
    marketing_site_url: str = Field(
        default="",
        description="Marketing site URL — OIDC audience for M2M provisioning",
    )

    transcription_worker_image: str = Field(
        default="",
        description="Container image for Whisper worker (e.g., gcr.io/PROJECT/pablo-transcription)",
    )
    transcription_backend_callback_url: str = Field(
        default="",
        description="Backend URL the Batch worker calls back to with the transcript",
    )
    transcription_queue_location: str = Field(
        default="us-central1",
        description="GCP region for Batch jobs and Cloud Tasks",
    )
    transcription_task_queue: str = Field(
        default="pablo-transcription",
        description="Cloud Tasks queue name for transcription polling",
    )
    soap_generation_task_queue: str = Field(
        default="pablo-soap-generation",
        description=(
            "Cloud Tasks queue for off-request SOAP generation. The upload "
            "route persists a PROCESSING session, enqueues a job here, and "
            "returns 202; a worker drains the queue and runs the LLM. The "
            "queue's maxConcurrentDispatches (set in queue config, not here) "
            "bounds how many generations run at once — the actual guard against "
            "saturating the request threadpool under concurrent uploads."
        ),
    )
    soap_generation_max_attempts: int = Field(
        default=5,
        description=(
            "Total delivery attempts for a SOAP-generation job before a "
            "transient failure (e.g. an LLM 429) is treated as terminal and the "
            "session is marked failed. Must match the pablo-soap-generation "
            "queue's maxAttempts: the worker reads the Cloud Tasks retry-count "
            "header and, on any attempt before the last, returns 5xx so the "
            "queue retries with backoff instead of failing the session."
        ),
    )
    document_finalize_task_queue: str = Field(
        default="pablo-soap-generation",
        description=(
            "Cloud Tasks queue for off-request patient-document finalize "
            "(GCS download + PyMuPDF + Document AI). Deliberately reuses the "
            "pablo-soap-generation queue rather than provisioning a dedicated "
            "one — same IAM, same maxAttempts/maxConcurrentDispatches profile, "
            "and this ships with zero new infra. Point this at a dedicated "
            "queue later if the two workloads need different concurrency or "
            "retry limits."
        ),
    )

    # NLI Model Settings
    nli_model_path: str = Field(
        default="cross-encoder/nli-deberta-v3-xsmall",
        description="NLI model name or local path",
    )

    # MiniCheck Model Settings
    minicheck_model_path: str = Field(
        default="lytang/MiniCheck-RoBERTa-Large",
        description="MiniCheck model name or local path for fact verification",
    )

    # EHR Navigation Settings
    ehr_navigate_daily_limit: int = Field(
        default=50,
        ge=1,
        description="Max LLM fallback calls per user per day for EHR navigation",
    )
    ehr_navigate_model: str = Field(
        default="gemini-2.5-flash-lite",
        description="Gemini model for EHR navigation LLM fallback",
    )

    # Per-user burst rate limits. These guard the expensive LLM- and
    # transcription-backed endpoints against a single authenticated caller
    # driving unbounded compute spend. They are per-deployment abuse
    # protection, not usage quotas — the defaults sit well above normal
    # interactive use.
    chat_rate_per_min: int = Field(
        default=20,
        ge=1,
        description="Max chat-send calls per user per minute",
    )
    chat_rate_per_hour: int = Field(
        default=300,
        ge=1,
        description="Max chat-send calls per user per hour",
    )
    upload_rate_per_min: int = Field(
        default=20,
        ge=1,
        description="Max audio-upload calls per user per minute",
    )
    upload_rate_per_hour: int = Field(
        default=300,
        ge=1,
        description="Max audio-upload calls per user per hour",
    )

    # Patient-context chat primitive (THERAPY-bhv).
    # When false, all /api/chat/* routes return 404 and the frontend
    # ChatPanel is not mounted. The migration runs unconditionally so
    # flipping this flag is a config change, not a deploy.
    enable_patient_chat: bool = Field(
        default=False,
        description=(
            "Enable the OSS patient-context chat primitive. Off by default; "
            "see docs/architecture/patient-context-chat-oss.md."
        ),
    )
    # Companion thin-client launch-intent handoff. When false, the
    # /api/launch/intent + /api/launch/redeem router is NOT mounted, so
    # both endpoints return 404. Kept off until the desktop companions
    # ship the verified-link redemption path. See
    # docs/design/companion-thin-client.md.
    enable_launch_intent: bool = Field(
        default=False,
        description="Mount POST /api/launch/intent + /api/launch/redeem.",
    )

    # Companion device-binding proof enforcement (DPoP, RFC 9449-style).
    # When false the DPoP middleware is a hard no-op pass-through, so the
    # validation layer can ship dark while native companions add signing
    # support. When true, any request carrying an ``X-Install-ID`` header
    # must also carry a valid ``DPoP`` proof signed by that device's
    # enrolled key; requests without the header keep working (legacy web
    # + un-upgraded companions). See
    # docs/design/companion-dpop-binding.md § Stage 2.
    enable_dpop_validation: bool = Field(
        default=False,
        description=(
            "Enforce X-Install-ID + DPoP proofs on authenticated routes. "
            "Off by default; hard no-op when disabled. See "
            "docs/design/companion-dpop-binding.md."
        ),
    )
    # Default chat model — used by ``resolve_chat_model`` when no
    # downstream resolver overrides it. Downstream consumers may swap
    # this per ``caller_feature_key``.
    ai_model: str = Field(
        default="gemini-3.1-pro-preview",
        description=(
            "Default Gemini model for chat (and any other generation "
            "surface that calls the default resolver). Per design doc "
            "§11.7, Pro-tier work (SOAP, justifications) targets this; "
            "Flash-tier chat falls through to ai_model_flash when set."
        ),
    )
    ai_model_flash: str = Field(
        default="gemini-3.5-flash",
        description=(
            "Flash-tier model used by chat callers by default. Cheaper "
            "than ``ai_model`` and sufficient for grounded chat. When "
            "unset, chat callers fall through to ``ai_model``."
        ),
    )
    note_max_output_tokens: int = Field(
        default=16384,
        description=(
            "Output-token budget for structured note generation (SOAP and "
            "other registry note types). Must be generous: thinking models "
            "(e.g. gemini-3.x pro) spend part of the output budget on "
            "reasoning tokens before emitting the JSON, so a value sized for "
            "a non-thinking model truncates the note on real, full-length "
            "transcripts. On truncation the generator retries once at twice "
            "this value. Tune per-deployment via NOTE_MAX_OUTPUT_TOKENS."
        ),
    )
    note_generation_temperature: float = Field(
        default=0.0,
        description=(
            "Sampling temperature for structured note (SOAP) generation. A "
            "clinical note is faithful extraction, not creative writing: the "
            "same transcript should yield the same note, and any sampling "
            "variance is a chance to draw an unfaithful note (e.g. inflating a "
            "diagnosis). Default 0.0 for reproducibility; source attribution is "
            "already deterministic. Tune via NOTE_GENERATION_TEMPERATURE."
        ),
    )
    note_thinking_budget: int | None = Field(
        default=None,
        description=(
            "Reasoning-token cap for SOAP (Call-1) generation. None uses the "
            "model's default dynamic thinking, which on a reasoning model (e.g. "
            "gemini-3.x pro) can spend 40-100s deliberating on a task the small, "
            "flat SOAP schema does not require — the dominant latency cost. A "
            "cap looks tempting but a faithfulness-eval sweep found it both "
            "DEGRADES faithfulness (the model needs the reasoning to resist "
            "inflating a diagnosis on adversarial transcripts) AND does not "
            "reduce latency (a low cap triggers a truncation retry, doubling "
            "it). Left uncapped by default; validate against the note-generation "
            "faithfulness eval before changing. Tune via NOTE_THINKING_BUDGET."
        ),
    )
    note_source_attribution_max_output_tokens: int = Field(
        default=32768,
        description=(
            "Output-token budget for the source-attribution call (Call 2) that "
            "grounds each SOAP claim to transcript segments. This is a separate, "
            "larger budget than note generation because a thinking model shares "
            "the output budget with its reasoning tokens: on a long indexed "
            "transcript the reasoning alone can exhaust a small budget and the "
            "call truncates with zero output (the claim->segment JSON never "
            "emits). Sized so reasoning (capped by "
            "NOTE_SOURCE_ATTRIBUTION_THINKING_BUDGET) plus the mapping always "
            "fit. Tune via NOTE_SOURCE_ATTRIBUTION_MAX_OUTPUT_TOKENS."
        ),
    )
    note_source_attribution_thinking_budget: int = Field(
        default=8192,
        description=(
            "Reasoning-token cap for the source-attribution call (Call 2). "
            "Attribution is a near-mechanical claim->segment mapping, so we cap "
            "thinking rather than letting it run to the model default — an "
            "uncapped budget on a long transcript consumes the whole output "
            "window on reasoning and returns zero characters. Capping it "
            "guarantees output room (max_output_tokens minus this) and bounds "
            "latency. Tune via NOTE_SOURCE_ATTRIBUTION_THINKING_BUDGET."
        ),
    )
    # LLM quota enforcement switch for the chat primitive
    # (THERAPY-f6eg). ``off`` (the default) records usage but never
    # rejects a turn; ``on`` lets ``LlmUsageMeter.check_quota`` consult
    # tenant-config limits. Operators who want their own caps flip
    # this on and subclass the meter. See design doc §11.6.
    llm_quota_enforcement: str = Field(
        default="off",
        description=(
            "Enable LLM quota enforcement on chat turns. ``off`` "
            "(default) records usage only; ``on`` lets the meter "
            "consult tenant-config limits and reject over-quota turns."
        ),
    )

    # LLM trace telemetry (OpenInference over OTLP/HTTP).
    # Unset endpoint => the tracer stays a no-op and no spans are
    # exported, so a deployment runs with LLM tracing off until an
    # operator points this at a collector. Any OTLP/HTTP backend works
    # (Phoenix, Honeycomb, Tempo, Cloud Trace) — switching is an endpoint
    # change, not a re-instrumentation. Spans carry metadata only (model,
    # token counts, latency, error class, request/user/tenant ids); never
    # prompt or response content.
    phoenix_collector_endpoint: str = Field(
        default="",
        description=(
            "OTLP/HTTP traces endpoint for content-free LLM spans (e.g. "
            "https://collector.example/v1/traces). Unset disables export."
        ),
    )
    llm_trace_use_id_token: bool = Field(
        default=True,
        description=(
            "Authenticate trace export with a Google-minted ID token whose "
            "audience is the collector origin (Cloud Run + run.invoker). "
            "Disable for a collector using static OTEL_EXPORTER_OTLP_* headers."
        ),
    )
    llm_trace_service_name: str = Field(
        default="pablo-backend",
        description="OTel resource service.name attached to exported LLM spans.",
    )
    llm_trace_project: str = Field(
        default="",
        description=(
            "Phoenix project the exported LLM spans land in (set as the "
            "OpenInference 'openinference.project.name' resource attribute). "
            "Use a per-deployment name (e.g. one per environment) to keep "
            "traces from different deployments from mixing. Empty groups them "
            "under the collector's 'default' project."
        ),
    )

    # AssemblyAI (batch transcription for SOAP pipeline)
    assemblyai_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="AssemblyAI API key (used when transcription_provider='assemblyai')",
    )
    assemblyai_speech_model: str = Field(
        default="best",
        description="AssemblyAI speech model, e.g. best, nano, slam-1.",
    )
    assemblyai_vad_enabled: bool = Field(
        default=False,
        description=(
            "Pre-trim each channel to speech-only regions before submitting "
            "(one job/channel, timestamps remapped). Requires decodable PCM/WAV; "
            "compressed uploads are always submitted whole. Off by default: the "
            "whole file is submitted per channel, which keeps the submit path "
            "simple. Turn on to trade a small recognition/cost win for that "
            "complexity once volume warrants it."
        ),
    )

    # Calendar Auto-Sync (Cloud Scheduler + Cloud Tasks)
    calendar_auto_sync_enabled: bool = Field(
        default=True,
        description="Enable periodic calendar sync via Cloud Scheduler",
    )
    calendar_sync_max_consecutive_failures: int = Field(
        default=5,
        description="Disable auto-sync for a feed after this many consecutive failures",
    )
    calendar_sync_task_queue: str = Field(
        default="pablo-calendar-sync",
        description="Cloud Tasks queue name for calendar sync fan-out",
    )
    calendar_sync_task_location: str = Field(
        default="us-central1",
        description="Cloud Tasks queue region",
    )

    # Google Calendar Integration
    google_calendar_client_id: str = Field(
        default="",
        description="Google OAuth client ID for Calendar integration",
    )
    google_calendar_client_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Google OAuth client secret for Calendar integration",
    )
    google_calendar_encryption_key: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "AES-256 encryption key (base64-encoded, 32 bytes) for "
            "encrypting OAuth tokens at rest. HIPAA requirement."
        ),
    )

    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL."""
        protocol = "rediss" if self.redis_ssl else "redis"
        password_part = (
            f":{self.redis_password.get_secret_value()}@"
            if self.redis_password.get_secret_value()
            else ""
        )
        return f"{protocol}://{password_part}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_saas(self) -> bool:
        """Check if running as a hosted edition (Solo or Practice)."""
        return self.pablo_edition in ("solo", "practice")

    @property
    def is_core(self) -> bool:
        """Check if running as self-hosted open-source (Pablo Core)."""
        return self.pablo_edition == "core"

    @property
    def is_development(self) -> bool:
        """Check if running in development environment."""
        return self.environment == "development"

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"

    @property
    def is_prod_project(self) -> bool:
        """True if running against the production project.

        Keys on the PROJECT id, not the environment string: a deployment may
        deliberately run non-prod projects with ENVIRONMENT=production so
        every code path behaves exactly as it will in production, and this
        property's only consumers are the reserved test-identity bypasses —
        which must be scoped by which project holds real data, not by how
        the environment is labeled. A deployment with no project id at all
        (self-hosted off GCP) falls back to the environment string, so
        "production" still means no test bypasses there.
        """
        if self.gcp_project_id:
            return bool(_PROD_PROJECT_PATTERN.search(self.gcp_project_id))
        return self.environment == "production"

    @property
    def effective_firebase_project_id(self) -> str:
        """Firebase project ID, falling back to GCP project ID."""
        return self.firebase_project_id or self.gcp_project_id


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


# Global settings instance for backwards compatibility
# Prefer using get_settings() for dependency injection
settings = get_settings()
