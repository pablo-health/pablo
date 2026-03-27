# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Transcription service configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TranscriptionSettings(BaseSettings):
    """Configuration for the transcription worker service."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Whisper model
    whisper_model_size: str = Field(
        default="large-v3-turbo",
        description="Whisper model size (tiny, base, small, medium, large-v3, large-v3-turbo)",
    )
    whisper_device: str = Field(
        default="auto",
        description="Device for inference: 'cuda', 'cpu', or 'auto'",
    )
    whisper_compute_type: str = Field(
        default="float16",
        description="Compute type: float16 (GPU), int8 (CPU), float32 (fallback)",
    )

    # GCS
    gcs_audio_bucket: str = Field(
        default="pablo-audio",
        description="GCS bucket for encrypted audio uploads",
    )

    # Backend callback
    backend_url: str = Field(
        default="http://localhost:8000",
        description="Pablo backend URL for transcription-complete callback",
    )

    # Worker
    port: int = Field(default=8080, description="Worker HTTP port")
    log_level: str = Field(default="INFO", description="Logging level")

    # GCP project (for IAM auth)
    gcp_project_id: str = Field(default="", description="GCP project ID")
