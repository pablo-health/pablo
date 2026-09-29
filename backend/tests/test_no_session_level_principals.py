# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""No principal is armed for a whole connection where the app can inherit it.

Every row-level policy reads ``app.current_user_id`` or
``app.current_patient_id`` to decide who is asking. The engine arms both
transaction-locally (``set_config(..., true)``), so they end with the request's
transaction. Set session-level — ``set_config(..., false)``, or a plain ``SET``
— one outlives the transaction and rides the pooled connection into whoever
draws it next. The pool's checkin listener resets both as a backstop; these
two rules keep code from relying on the backstop.

1. **Application code** never sets a principal session-level.
2. **A test that uses the app's shared engine or session** — ``get_engine()``,
   the app's session factory or request session, or the app itself
   (``app.main``) — never sets one session-level either. That pool is the one
   every later module shares, and a test once did exactly this there, so
   later modules started out armed as its user.

Tests that only use their own ``create_engine(...)`` are exempt: that pool is
theirs, RLS tests switch principals across transactions on it on purpose, and
the principal cannot outlive the module. The rule works per file, so a file
that both drives the app and sets a principal session-level on its own engine
is listed in ``_OWN_ENGINE_ONLY`` with the reason, rather than guessed at.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_BACKEND = Path(__file__).resolve().parents[1]
_APP = _BACKEND / "app"
_TEST_TREES = (_BACKEND / "tests", _BACKEND / "tests_integration")

#: A principal set for the whole connection: ``set_config('app.current_user_id',
#: <anything>, false)`` (or the patient one), whatever the spacing or case, or a
#: plain ``SET`` / ``SET SESSION`` of either. ``SET LOCAL`` is transaction-local
#: and does not match.
_SESSION_LEVEL = re.compile(
    r"set_config\(\s*'app\.current_(?:user|patient)_id'\s*,[^)]*,\s*false\s*\)"
    r"|\bSET\s+(?:SESSION\s+)?app\.current_(?:user|patient)_id\b",
    re.IGNORECASE,
)

#: The app's own engine, session or app: anything that draws on the shared
#: pool rather than a pool the test created.
_SHARED_ENGINE = re.compile(
    r"\bget_engine\(|\bget_session_factory\(|\bget_db_session\(|"
    r"\bcreate_standalone_session\(|\bapp\.main\b"
)

#: Files that drive the app AND set a principal session-level, where every one
#: of those settings runs on the module's own ``create_engine(...)`` engine and
#: never on the shared pool. Checked by reading each site; keep the reason
#: current if the file changes.
_OWN_ENGINE_ONLY: dict[str, str] = {
    "tests_integration/api/test_import_archive_api.py": (
        "_count and the note check read through the module's own create_engine "
        "connection; the app is only driven over HTTP"
    ),
    "tests_integration/api/test_patients_api_e2e.py": (
        "the post-request row check runs on the module's own create_engine "
        "engine.begin() connection"
    ),
    "tests_integration/api/test_portal_sign_in_e2e.py": (
        "the practice fixture seeds its patient on the module's own create_engine connection"
    ),
    "tests_integration/api/test_restricted_note_companion_routes.py": (
        "the seed fixture writes on the module's own create_engine connection"
    ),
    "tests_integration/api/test_soap_async_upload_e2e.py": (
        "_count, _session_status and _note_count_for_session read on the "
        "module's own create_engine connection"
    ),
    "tests_integration/database/test_patient_charges_db.py": (
        "_seed_patient and _armed use the module's own create_engine; _armed "
        "holds one connection across transactions on purpose"
    ),
    "tests_integration/database/test_patient_idor_http.py": (
        "the fixture seeds patients on the module's own create_engine connection"
    ),
    "tests_integration/database/test_portal_cross_practice_isolation.py": (
        "the count helpers and seeding run on the module's own create_engine connections"
    ),
}


def _session_level_lines(text: str) -> list[int]:
    return [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if _SESSION_LEVEL.search(line)
    ]


def _shared_engine_offences(text: str) -> list[int]:
    """Lines setting a principal session-level, in a file that uses the app's
    shared engine or session. Empty when the file uses neither."""
    return _session_level_lines(text) if _SHARED_ENGINE.search(text) else []


# --- self-checks: the patterns match what they are for ----------------------


@pytest.mark.parametrize(
    ("sql", "flagged"),
    [
        ("SELECT set_config('app.current_user_id', :uid, false)", True),
        ("SELECT set_config('app.current_patient_id', :pid, FALSE)", True),
        ("SELECT set_config( 'app.current_user_id' , :u ,  false )", True),
        ("SET app.current_user_id = 'abc'", True),
        ("SET SESSION app.current_patient_id TO 'abc'", True),
        ("SELECT set_config('app.current_user_id', :uid, true)", False),
        ("SELECT set_config('app.current_patient_id', :pid, true)", False),
        ("SET LOCAL app.current_user_id = 'abc'", False),
        ("SELECT set_config('search_path', :p, false)", False),
        ("SET search_path = practice_abc, platform, public", False),
    ],
)
def test_the_session_level_pattern_catches_what_it_is_for(sql: str, *, flagged: bool) -> None:
    assert bool(_SESSION_LEVEL.search(sql)) is flagged


_ARMS_FOR_THE_CONNECTION = (
    "conn.execute(text(\"SELECT set_config('app.current_user_id', :u, false)\"))"
)


@pytest.mark.parametrize(
    ("source", "offending"),
    [
        # On the app's shared pool: flagged.
        (f"engine = get_engine()\n{_ARMS_FOR_THE_CONNECTION}\n", [2]),
        (f"session = get_db_session()\n{_ARMS_FOR_THE_CONNECTION}\n", [2]),
        (f"s = create_standalone_session(schema)\n{_ARMS_FOR_THE_CONNECTION}\n", [2]),
        (f"from app.main import app\n{_ARMS_FOR_THE_CONNECTION}\n", [2]),
        ("import app.main\nconn.execute(text('SET app.current_patient_id = :p'))\n", [2]),
        # Only its own engine: exempt.
        (f"eng = create_engine(_DB_URL)\n{_ARMS_FOR_THE_CONNECTION}\n", []),
        # Shared pool, but transaction-local: fine.
        (
            "session = get_db_session()\n"
            "conn.execute(text(\"SELECT set_config('app.current_user_id', :u, true)\"))\n",
            [],
        ),
    ],
)
def test_the_shared_engine_rule_flags_only_the_shared_pool(
    source: str, offending: list[int]
) -> None:
    assert _shared_engine_offences(source) == offending


# --- the rules ---------------------------------------------------------------


def test_no_application_code_sets_a_principal_for_the_whole_connection() -> None:
    offenders = [
        f"{path.relative_to(_BACKEND)}:{number}"
        for path in sorted(_APP.rglob("*.py"))
        for number in _session_level_lines(path.read_text())
    ]
    assert offenders == [], (
        "Arm principals transaction-locally (arm_current_user_id / "
        f"arm_current_patient_id, or set_config(..., true)): {offenders}"
    )


def test_no_test_sets_a_principal_for_the_whole_connection_on_the_shared_pool() -> None:
    this_file = Path(__file__).resolve()
    offenders: list[str] = []
    for tree in _TEST_TREES:
        for path in sorted(tree.rglob("*.py")):
            relative = str(path.relative_to(_BACKEND))
            if path.resolve() == this_file or relative in _OWN_ENGINE_ONLY:
                continue
            offenders += [
                f"{relative}:{number}" for number in _shared_engine_offences(path.read_text())
            ]
    assert offenders == [], (
        "This file uses the app's shared engine or session and sets a principal "
        "for the whole connection. Arm it transaction-locally (set_config(..., "
        "true) inside the transaction, or arm_current_user_id on an ORM session). "
        "If every such setting is on the module's own create_engine(...), add the "
        f"file to _OWN_ENGINE_ONLY with the reason: {offenders}"
    )


def test_every_allowlisted_file_still_needs_to_be() -> None:
    """An entry whose file no longer sets a principal session-level, or no
    longer touches the shared pool, is stale and should go."""
    stale = [
        relative
        for relative in _OWN_ENGINE_ONLY
        if not (_BACKEND / relative).exists()
        or not _shared_engine_offences((_BACKEND / relative).read_text())
    ]
    assert stale == [], f"remove these from _OWN_ENGINE_ONLY: {stale}"
