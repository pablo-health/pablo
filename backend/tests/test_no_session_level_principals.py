# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Application code never arms a principal for the whole connection.

Every row-level policy reads ``app.current_user_id`` or
``app.current_patient_id`` to decide who is asking. The engine arms both
transaction-locally (``set_config(..., true)``), so they end with the request's
transaction. A session-level ``set_config(..., false)`` would outlive it and
ride the pooled connection into the next request. The pool's checkin listener
resets both as a backstop; this keeps the code from relying on the backstop.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_APP = Path(__file__).resolve().parents[1] / "app"

#: ``set_config('app.current_user_id', <anything>, false)`` — and the patient
#: one — whatever the spacing or the case of ``false``.
_SESSION_LEVEL = re.compile(
    r"set_config\(\s*'app\.current_(?:user|patient)_id'\s*,[^)]*,\s*false\s*\)",
    re.IGNORECASE,
)


@pytest.mark.parametrize(
    ("sql", "flagged"),
    [
        ("SELECT set_config('app.current_user_id', :uid, false)", True),
        ("SELECT set_config('app.current_patient_id', :pid, FALSE)", True),
        ("SELECT set_config( 'app.current_user_id' , :u ,  false )", True),
        ("SELECT set_config('app.current_user_id', :uid, true)", False),
        ("SELECT set_config('app.current_patient_id', :pid, true)", False),
        ("SELECT set_config('search_path', :p, false)", False),
    ],
)
def test_the_pattern_catches_what_it_is_for(sql: str, *, flagged: bool) -> None:
    assert bool(_SESSION_LEVEL.search(sql)) is flagged


def test_no_application_code_sets_a_principal_for_the_whole_connection() -> None:
    offenders = [
        f"{path.relative_to(_APP.parent)}:{number}"
        for path in sorted(_APP.rglob("*.py"))
        for number, line in enumerate(path.read_text().splitlines(), start=1)
        if _SESSION_LEVEL.search(line)
    ]
    assert offenders == [], (
        "Arm principals transaction-locally (arm_current_user_id / "
        f"arm_current_patient_id, or set_config(..., true)): {offenders}"
    )
