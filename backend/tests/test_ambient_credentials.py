# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The suite runs without the machine's Google credentials, as it does on CI.

These pass trivially on CI, which has no credentials to hide. They exist for a
workstation logged in with ``gcloud auth application-default login``: there,
without the isolation in ``tests/ambient_credentials.py``, a model call a test
forgot to stub is a real request to gcloud's default project.
"""

from __future__ import annotations

import os

import google.auth
import pytest
from google.auth import exceptions
from google.auth.compute_engine import _metadata


def test_application_default_credentials_are_not_found() -> None:
    with pytest.raises(exceptions.DefaultCredentialsError):
        google.auth.default()


def test_no_key_file_is_exported_to_the_run() -> None:
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


def test_the_isolation_ran_before_google_auth_was_imported() -> None:
    # ``NO_GCE_CHECK`` is read once, when ``_metadata`` is imported. If the
    # conftest set it too late, every credential lookup in the suite would
    # stall on a metadata-server probe.
    assert _metadata._NO_GCE_CHECK is True
