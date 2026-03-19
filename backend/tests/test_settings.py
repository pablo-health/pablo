# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for application settings."""

import pytest
from app.settings import Settings
from pydantic import ValidationError


class TestRatingFeedbackThreshold:
    """Test rating_feedback_required_below configuration."""

    def test_default_value(self) -> None:
        """Test default value is 5."""
        settings = Settings()
        assert settings.rating_feedback_required_below == 5

    def test_valid_range(self) -> None:
        """Test valid values 1-5 are accepted."""
        for value in range(1, 6):
            settings = Settings(rating_feedback_required_below=value)
            assert settings.rating_feedback_required_below == value

    def test_below_range_rejected(self) -> None:
        """Test value below 1 is rejected."""
        with pytest.raises(ValidationError):
            Settings(rating_feedback_required_below=0)

    def test_above_range_rejected(self) -> None:
        """Test value above 5 is rejected."""
        with pytest.raises(ValidationError):
            Settings(rating_feedback_required_below=6)

    def test_environment_variable_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test environment variable override works."""
        monkeypatch.setenv("RATING_FEEDBACK_REQUIRED_BELOW", "3")
        settings = Settings()
        assert settings.rating_feedback_required_below == 3


class TestBraintrustSettings:
    """Test Braintrust configuration settings."""

    def test_braintrust_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Braintrust is disabled when API key not provided."""
        # Clear any existing BRAINTRUST_API_KEY from environment
        monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
        # Create settings without loading .env file
        settings = Settings(_env_file=None)  # type: ignore[call-arg]
        assert not settings.is_braintrust_enabled

    def test_braintrust_enabled_with_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Braintrust is enabled when API key is provided."""
        monkeypatch.setenv("BRAINTRUST_API_KEY", "bt-test-key-123")
        settings = Settings()
        assert settings.is_braintrust_enabled
        assert settings.braintrust_api_key is not None
        assert settings.braintrust_api_key.get_secret_value() == "bt-test-key-123"

    def test_braintrust_disabled_with_empty_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test Braintrust is disabled when API key is empty string."""
        monkeypatch.setenv("BRAINTRUST_API_KEY", "")
        settings = Settings()
        assert not settings.is_braintrust_enabled

    def test_default_project_name(self) -> None:
        """Test default Braintrust project name."""
        settings = Settings()
        assert settings.braintrust_project_name == "Pablo"

    def test_custom_project_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test custom Braintrust project name from environment."""
        monkeypatch.setenv("BRAINTRUST_PROJECT_NAME", "Custom Project")
        settings = Settings()
        assert settings.braintrust_project_name == "Custom Project"
