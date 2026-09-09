# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The resolver reports WHY it could not resolve a practice schema.

When resolution returns nothing the request continues on
``DEFAULT_PRACTICE_SCHEMA``. Deployments that keep practice data in
per-practice schemas will not find those tables there, so an unqualified query
reports a missing relation — an error naming the symptom rather than the
cause. Returning a reason lets the caller record the cause instead.

The resolution behaviour is unchanged. What these cover is that each way of
arriving at "no schema" is distinguishable, and that the reason never carries
an address or an error message.

They call the resolver directly with a stub request rather than patching it.
Patching the function under test would mean the assertions never exercise the
code they are about — a shape worth avoiding in the tests that describe it.
"""

from __future__ import annotations

import types

from app.db.middleware import (
    UNRESOLVED_LOOKUP_RAISED,
    UNRESOLVED_NO_EMAIL,
    UNRESOLVED_NO_MAPPING,
    UNRESOLVED_UNAUTHENTICATED,
    _resolve_schema_from_request,
)


def _request(identity: object | None) -> types.SimpleNamespace:
    """The only thing the resolver reads is ``request.state.verified_identity``."""
    return types.SimpleNamespace(state=types.SimpleNamespace(verified_identity=identity))


def _identity(email: str | None) -> types.SimpleNamespace:
    return types.SimpleNamespace(email=email)


def test_unauthenticated_is_the_one_legitimate_unresolved_case() -> None:
    """Public routes carry no identity and read no practice data, so this case
    stays quiet — logging it would bury the ones worth reading."""
    schema, reason = _resolve_schema_from_request(_request(None))

    assert schema is None
    assert reason == UNRESOLVED_UNAUTHENTICATED


def test_identity_without_an_email_is_reported_separately() -> None:
    schema, reason = _resolve_schema_from_request(_request(_identity(None)))

    assert schema is None
    assert reason == UNRESOLVED_NO_EMAIL


def test_authenticated_with_no_practice_mapping_is_named(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A valid identity that maps to no practice — reported on its own so it is
    distinguishable from a lookup that failed."""
    monkeypatch.setattr("app.auth.service._resolve_practice_from_email", lambda _email: None)

    schema, reason = _resolve_schema_from_request(_request(_identity("someone@example.invalid")))

    assert schema is None
    assert reason == UNRESOLVED_NO_MAPPING


def test_a_raised_lookup_is_reported_not_swallowed_silently(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """A lookup that raises is still swallowed, but it is reported rather than
    passing unremarked — the exception type is what makes it readable."""

    def _boom(_email: str) -> None:
        raise TimeoutError("pool exhausted")

    monkeypatch.setattr("app.auth.service._resolve_practice_from_email", _boom)

    schema, reason = _resolve_schema_from_request(_request(_identity("someone@example.invalid")))

    assert schema is None
    assert reason.startswith(UNRESOLVED_LOOKUP_RAISED)
    assert "TimeoutError" in reason, "the exception type is what makes this readable"


def test_the_reason_never_carries_the_email_or_the_error_message(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """The reason is logged, and an exception message can echo the address or
    row that caused it, so only the type is carried."""

    def _boom(_email: str) -> None:
        raise ValueError("no mapping for patient jane.doe@example.invalid")

    monkeypatch.setattr("app.auth.service._resolve_practice_from_email", _boom)

    _schema, reason = _resolve_schema_from_request(_request(_identity("jane.doe@example.invalid")))

    assert "jane.doe@example.invalid" not in reason
    assert "no mapping for patient" not in reason


def test_a_resolved_identity_returns_its_schema(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "app.auth.service._resolve_practice_from_email",
        lambda _email: ("practice-id", "practice_abc123"),
    )

    schema, reason = _resolve_schema_from_request(_request(_identity("someone@example.invalid")))

    assert schema == "practice_abc123"
    assert reason == "resolved"
