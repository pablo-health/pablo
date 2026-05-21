# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the IAM-API-backed signing path in signed_upload.

Other behavior (PUT/GET URL shape, header constraints, blob metadata)
is exercised by test_patient_documents_service.py with a fake GCS
client. This file narrowly covers the credentials-introspection logic
added for THERAPY-vapd (signed-URL signing fails on Cloud Run because
metadata-server credentials don't carry a private key).
"""

from unittest.mock import MagicMock, patch

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
    mock_default.side_effect = google.auth.exceptions.DefaultCredentialsError(
        "no ADC"
    )
    assert _iam_signing_kwargs() == {}


@patch("google.auth.default")
def test_returns_empty_for_default_service_account_marker(
    mock_default: MagicMock,
) -> None:
    """Credentials reporting `service_account_email='default'` still
    can't sign — but neither can they delegate (no real SA email).
    Fall through to self-sign so the underlying error is the
    library's clear message, not an IAM-API misroute.
    """
    creds = MagicMock()
    creds.service_account_email = "default"
    mock_default.return_value = (creds, "project")
    assert _iam_signing_kwargs() == {}


@patch("google.auth.transport.requests.Request")
@patch("google.auth.default")
def test_returns_iam_signing_kwargs_for_metadata_server_creds(
    mock_default: MagicMock,
    _mock_request_cls: MagicMock,  # noqa: PT019 — silences refresh() arg
) -> None:
    """Cloud Run / GKE / GCE: delegate signature to IAM signBlob."""
    creds = MagicMock()
    creds.service_account_email = "pablo-backend@proj.iam.gserviceaccount.com"
    creds.token = "ya29.fake-token"
    mock_default.return_value = (creds, "project")

    out = _iam_signing_kwargs()

    creds.refresh.assert_called_once()
    assert out == {
        "service_account_email": (
            "pablo-backend@proj.iam.gserviceaccount.com"
        ),
        "access_token": "ya29.fake-token",
    }
