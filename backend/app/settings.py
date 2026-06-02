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

    @property
    def e2e_test_emails(self) -> set[str]:
        """Parse comma-separated E2E_TEST_EMAILS into a set."""
        if not self.e2e_test_emails_raw:
            return set()
        return {e.strip() for e in self.e2e_test_emails_raw.split(",") if e.strip()}

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

    # Authentication Mode
    auth_mode: Literal["standard", "iap"] = Field(
        default="standard",
        description=(
            "Authentication mode. "
            "'standard' = Firebase Auth with optional MFA. "
            "'iap' = Google Cloud IAP at load balancer; "
            "REQUIRE_MFA can be false since IAP handles access control."
        ),
    )
    iap_audience: str = Field(
        default="",
        description=(
            "Expected audience claim for IAP JWT verification. "
            "Format: /projects/{number}/global/backendServices/{id}. "
            "Required when auth_mode=iap."
        ),
    )

    # Pluggable OIDC auth backend (additive — independent of auth_mode).
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

    # Database Backend
    database_backend: Literal["postgres"] = Field(
        default="postgres",
        description="Primary database backend (PostgreSQL with schema-per-practice multi-tenancy).",
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

    high_rating_threshold: int = Field(
        default=4,
        ge=1,
        le=5,
        description="Sessions with rating ≥ this are sampled for eval export",
    )
    high_rating_sample_rate: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Probability (0.0-1.0) of queueing high-rated sessions",
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
        default="whisper",
        description=(
            "Transcription provider for session audio. "
            "'whisper' = self-hosted faster-whisper on GCP Batch spot GPUs. "
            "'assemblyai' = AssemblyAI batch API (lower ops, higher per-session cost)."
        ),
    )
    transcription_audio_bucket: str = Field(
        default="pablo-audio",
        description="GCS bucket for encrypted audio uploads",
    )
    # Patient document upload (THERAPY-ak6m.2). When unset, the
    # /api/patients/{id}/documents surface returns 503 with a clear
    # configuration message — keeps self-hosters who haven't provisioned
    # a bucket from quietly getting a broken upload flow.
    patient_documents_gcs_bucket: str | None = Field(
        default=None,
        description=(
            "GCS bucket for clinician-uploaded patient documents (PDFs, "
            "PNG/JPEG scans). Per-tenant prefix: "
            "gs://<bucket>/<tenant_id>/<uuid>. Leave unset to disable "
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
        """True if running against the production GCP project."""
        if self.environment == "production":
            return True
        return bool(_PROD_PROJECT_PATTERN.search(self.gcp_project_id))

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
