# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""
Application settings and configuration management.

Uses pydantic-settings for type-safe environment variable handling.
Configuration can be loaded from .env files (local dev) or environment variables (production).

HIPAA Compliance: Manages security settings including TLS enforcement
and environment-specific configurations for PHI protection.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    hsts_max_age: int = Field(
        default=31536000,  # 1 year in seconds
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
            "Require BAA acceptance before PHI access. "
            "SaaS deployments set this to True via setup.sh. "
            "Self-hosted (Core) defaults to False — therapist signs BAA "
            "directly with their cloud provider."
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

    # Sign-up Restriction Settings
    restrict_signups: bool = Field(
        default=False,
        description=(
            "Only allowlisted emails can sign in. "
            "SaaS deployments set this to True via setup.sh. "
            "Self-hosted (Core) defaults to False — solo therapist "
            "doesn't need an allowlist."
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

    # Stripe Settings (SaaS billing)
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

    # Multi-Tenancy Settings (Identity Platform)
    multi_tenancy_enabled: bool = Field(
        default=False,
        description=(
            "Enable Identity Platform multi-tenancy. "
            "When enabled, JWTs must contain a firebase.tenant claim "
            "and requests are routed to per-practice PostgreSQL schemas."
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
    compliance_hard_purge_enabled: bool = Field(
        default=False,
        description=(
            "Hosted/SaaS only: allow the hard-purge job to physically delete "
            "soft-deleted patients past the retention window and write the "
            "hosted minimal retention stub row in the compliance schema. "
            "Pablo Core default is False — CEs keep soft-delete plus audit "
            "trail only. Set via COMPLIANCE_HARD_PURGE_ENABLED on the "
            "retention Cloud Run Job."
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
        """Check if running as a SaaS edition (Solo or Practice)."""
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
