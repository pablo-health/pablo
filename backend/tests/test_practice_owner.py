# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Recording a practice's owner never raises on an id that is not an account.

The auth resolve path falls back to the provider's raw subject id for an
account with no identity row yet, and ``owner_user_id`` is a uuid column. The
rules of the write itself are proved against the real registry in
``tests_integration/database/test_practice_owner_db.py``; what belongs here is
that an id of the wrong shape is turned away before any statement runs.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from app.db.practice_owner import record_owner_on_sign_in


@pytest.mark.parametrize("user_id", ["", "firebase-uid-abc123"])
def test_an_unresolved_account_id_records_nothing(
    user_id: str, caplog: pytest.LogCaptureFixture
) -> None:
    with (
        patch("app.db.create_standalone_session") as open_session,
        caplog.at_level(logging.DEBUG, logger="app.db.practice_owner"),
    ):
        recorded = record_owner_on_sign_in("practice-abc", "dr@example.com", user_id)

    assert recorded is False
    open_session.assert_not_called()
    # Noted for whoever is debugging a missing owner, at a level that keeps it
    # out of an ordinary request log; the id itself is not worth repeating.
    notes = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("practice-abc" in note for note in notes)
    if user_id:
        assert all(user_id not in note for note in notes)
