# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the IAM-API-backed signing path in signed_upload.

Other behavior (PUT/GET URL shape, header constraints, blob metadata)
is exercised by test_patient_documents_service.py with a fake GCS
client. This file narrowly covers the credentials-introspection logic
added for THERAPY-vapd (signed-URL signing fails on Cloud Run because
metadata-server credentials don't carry a private key).
"""

from unittest.mock import MagicMock, patch

import google.auth.compute_engine
import google.auth.exceptions
from app.services.signed_upload import _iam_signing_kwargs


@patch("google.auth.default")
def test_returns_empty_for_local_adc_credentials(
    mock_default: MagicMock,
) -> None:
    """User credentials (gcloud ADC) have a private key — self-sign."""
    creds = MagicMock(spec=[])  # no service_account_email attribute
    mock_default.return_value = (creds, "project")
    assert _iam_signing_kwargs() == {}


@patch("google.auth.default")
def test_returns_empty_when_no_adc_configured(
    mock_default: MagicMock,
) -> None:
    """CI test runners have no ADC — fall through quietly."""
    mock_default.side_effect = google.auth.exceptions.DefaultCredentialsError("no ADC")
    assert _iam_signing_kwargs() == {}


@patch("google.auth.compute_engine._metadata.get_service_account_info")
@patch("google.auth.default")
def test_resolves_default_marker_via_metadata_lookup(
    mock_default: MagicMock,
    mock_metadata: MagicMock,
) -> None:
    """ComputeEngineCredentials with service_account_email='default'
    must look up the real SA email via the metadata server before
    delegating to IAM signBlob. Otherwise IAM rejects with 'unknown SA'.
    """
    creds = MagicMock(spec=google.auth.compute_engine.Credentials)
    creds.service_account_email = "default"
    creds.token = "ya29.fake-token"
    mock_default.return_value = (creds, "project")
    mock_metadata.return_value = {
        "email": "pablo-backend@proj.iam.gserviceaccount.com",
    }

    out = _iam_signing_kwargs()

    mock_metadata.assert_called_once()
    creds.refresh.assert_called_once()
    assert out == {
        "service_account_email": ("pablo-backend@proj.iam.gserviceaccount.com"),
        "access_token": "ya29.fake-token",
    }


@patch("google.auth.default")
def test_returns_iam_signing_kwargs_for_compute_engine_creds_with_explicit_email(
    mock_default: MagicMock,
) -> None:
    """If service_account_email already resolves to a real address
    (rare but possible), use it without an extra metadata round-trip.
    """
    creds = MagicMock(spec=google.auth.compute_engine.Credentials)
    creds.service_account_email = "pablo-backend@proj.iam.gserviceaccount.com"
    creds.token = "ya29.fake-token"
    mock_default.return_value = (creds, "project")

    out = _iam_signing_kwargs()

    creds.refresh.assert_called_once()
    assert out == {
        "service_account_email": ("pablo-backend@proj.iam.gserviceaccount.com"),
        "access_token": "ya29.fake-token",
    }


@patch("google.auth.compute_engine._metadata.get_service_account_info")
@patch("google.auth.default")
def test_metadata_lookup_failure_falls_through(
    mock_default: MagicMock,
    mock_metadata: MagicMock,
) -> None:
    """If the metadata server is unreachable, fall through so the
    library's canonical 'you need a private key' AttributeError
    surfaces — not a confusing IAM 404.
    """
    creds = MagicMock(spec=google.auth.compute_engine.Credentials)
    creds.service_account_email = "default"
    mock_default.return_value = (creds, "project")
    mock_metadata.side_effect = Exception("metadata unreachable")

    assert _iam_signing_kwargs() == {}
