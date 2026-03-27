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

from pydantic import Field, SecretStr, ValidationInfo, field_validator
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
            "and requests are routed to per-practice Firestore databases."
        ),
    )
    admin_database: str = Field(
        default="(default)",
        description=(
            "Firestore database for the admin/control plane "
            "(tenant mappings, allowlist, provisioning log)."
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

    # Database Settings (Google Cloud Firestore)
    gcp_project_id: str = Field(
        default="",
        description="GCP project ID for Firestore",
    )
    firestore_database: str = Field(
        default="(default)",
        description="Firestore database name",
    )

    # Firebase Authentication
    firebase_project_id: str = Field(
        default="",
        description="Firebase project ID for token verification (falls back to gcp_project_id)",
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

    @field_validator("high_rating_threshold")
    @classmethod
    def validate_thresholds(cls, v: int, info: ValidationInfo) -> int:
        """Ensure high threshold is greater than low threshold."""
        if "low_rating_threshold" in info.data and v <= info.data["low_rating_threshold"]:
            raise ValueError("high_rating_threshold must be greater than low_rating_threshold")
        return v

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
            "When enabled, audio uploads are accepted and queued for Whisper processing "
            "on GPU worker instances."
        ),
    )
    transcription_audio_bucket: str = Field(
        default="pablo-audio",
        description="GCS bucket for encrypted audio uploads",
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
        description="GCP region for Batch jobs",
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
