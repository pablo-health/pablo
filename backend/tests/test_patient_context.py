# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for the patient principal and its resolver seam.

The load-bearing cases here are the *separation* ones: a clinician
credential must not produce a patient principal, and a patient credential
must not satisfy a clinician dependency. Everything else in this module
supports those two.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from app.auth import patient_context as patient_context_module
from app.auth.patient_context import (
    AuthStrength,
    PatientContext,
    PatientCredential,
    PatientResolverRegistry,
    _credential_from_request,
    get_patient_context,
    get_patient_resolver_registry,
)
from app.auth.service import TenantContext, verify_token
from app.db import middleware as middleware_module
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from firebase_admin import auth as firebase_auth

_SCHEMA = "practice_pc_test"
_PATIENT_ID = "3f0c3a52-1d6e-4a9f-9c6d-8b3f2c1a7e50"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeResolver:
    """A front door that accepts one exact credential value."""

    def __init__(
        self,
        *,
        kind: str = "bearer",
        accepts: str = "good-token",
        patient_id: str = _PATIENT_ID,
        strength: AuthStrength = AuthStrength.STEPPED_UP,
    ) -> None:
        self.credential_kind = kind
        self._accepts = accepts
        self._patient_id = patient_id
        self._strength = strength
        self.calls: list[PatientCredential] = []

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        self.calls.append(credential)
        if credential.value != self._accepts:
            return None
        return PatientContext(
            patient_id=self._patient_id,
            practice_schema=_SCHEMA,
            credential_kind=self.credential_kind,
            auth_strength=self._strength,
        )


class _ExplodingResolver:
    """A front door whose identity provider is down."""

    def __init__(self, kind: str = "bearer") -> None:
        self.credential_kind = kind
        self.calls = 0

    def resolve(self, credential: PatientCredential) -> PatientContext | None:
        self.calls += 1
        msg = "identity provider unreachable"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Credential normalization
# ---------------------------------------------------------------------------


class _StubRequest:
    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}

        class _State:
            pass

        self.state = _State()


class TestCredentialFromRequest:
    def test_bearer_header_becomes_a_lowercased_kind(self) -> None:
        credential = _credential_from_request(
            _StubRequest({"authorization": "Bearer abc.def"})  # type: ignore[arg-type]
        )
        assert credential == PatientCredential(kind="bearer", value="abc.def")

    def test_missing_header_is_no_credential(self) -> None:
        assert _credential_from_request(_StubRequest()) is None  # type: ignore[arg-type]

    def test_scheme_without_value_is_no_credential(self) -> None:
        assert (
            _credential_from_request(_StubRequest({"authorization": "Bearer   "}))  # type: ignore[arg-type]
            is None
        )

    def test_non_bearer_scheme_keeps_its_own_kind(self) -> None:
        """A future front door registers under its own scheme, not 'bearer'."""
        credential = _credential_from_request(
            _StubRequest({"authorization": "Widget host-assertion"})  # type: ignore[arg-type]
        )
        assert credential is not None
        assert credential.kind == "widget"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestPatientResolverRegistry:
    def test_resolves_through_a_registered_resolver(self) -> None:
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver())
        context = registry.resolve(PatientCredential(kind="bearer", value="good-token"))
        assert context is not None
        assert context.patient_id == _PATIENT_ID

    def test_unknown_kind_is_not_offered_to_any_resolver(self) -> None:
        registry = PatientResolverRegistry()
        resolver = _FakeResolver(kind="bearer")
        registry.register(resolver)
        assert registry.resolve(PatientCredential(kind="widget", value="good-token")) is None
        assert resolver.calls == []

    def test_first_resolver_to_claim_the_credential_wins(self) -> None:
        registry = PatientResolverRegistry()
        first = _FakeResolver(accepts="one", patient_id="patient-one")
        second = _FakeResolver(accepts="two", patient_id="patient-two")
        registry.register(first)
        registry.register(second)
        context = registry.resolve(PatientCredential(kind="bearer", value="two"))
        assert context is not None
        assert context.patient_id == "patient-two"
        # The credential was offered to the first resolver, which declined.
        assert len(first.calls) == 1

    def test_a_raising_resolver_aborts_rather_than_falling_through(self) -> None:
        """No auth-strength downgrade: a raiser must not hand off to a weaker door.

        ``None`` means "not my credential"; an exception means "I could
        not decide". If a could-not-decide fell through to the next
        resolver, an attacker who can make a strong front door raise —
        DoS its provider, or trip a parse error before the signature
        check — would be served by whatever weaker resolver sits behind
        it. Here the resolver behind the broken one WOULD have accepted
        the credential, and must not get the chance.
        """
        registry = PatientResolverRegistry()
        broken = _ExplodingResolver()
        would_have_accepted = _FakeResolver(accepts="good-token")
        registry.register(broken)
        registry.register(would_have_accepted)

        context = registry.resolve(PatientCredential(kind="bearer", value="good-token"))

        assert broken.calls == 1
        assert context is None, "resolution fell through a raiser to a resolver behind it"
        assert would_have_accepted.calls == [], "the weaker door was consulted anyway"

    def test_a_raising_resolver_alone_yields_no_principal(self) -> None:
        registry = PatientResolverRegistry()
        registry.register(_ExplodingResolver())
        assert registry.resolve(PatientCredential(kind="bearer", value="anything")) is None


class TestResolverInterfaceHasNoIdpTypes:
    """Acceptance criterion 3, as an executable check rather than a diff read.

    The seam exists so a second front door is an adapter, not a rewrite.
    That only holds while the protocol's own surface stays vendor-free.
    """

    def test_protocol_signature_mentions_no_identity_provider(self) -> None:
        """The *interface* must be vendor-free. Prose about vendors is not.

        The docstring is stripped before checking, deliberately. It carries
        a warning that names Firebase as a trap for future resolver authors
        — "whatever you mint must be structurally unacceptable to the
        clinician verifiers" — and that warning is worth more than a
        blanket no-vendor-words rule. What has to stay clean is the
        signature: the members and annotations a second front door must
        satisfy.
        """
        protocol = patient_context_module.PatientPrincipalResolver
        source = inspect.getsource(protocol)
        docstring = inspect.getdoc(protocol) or ""
        signature_only = source
        for line in docstring.splitlines():
            signature_only = signature_only.replace(line, "")

        lowered = signature_only.lower()
        for vendor in ("firebase", "oidc", "saml", "jwt", "smart", "oauth"):
            assert vendor not in lowered, f"resolver protocol signature names {vendor}"

        # The annotations name only this module's own types.
        assert set(protocol.resolve.__annotations__) == {"credential", "return"}

    def test_the_credential_carries_opaque_material_only(self) -> None:
        fields = set(PatientCredential.__dataclass_fields__)
        assert fields == {"kind", "value", "parameters"}


# ---------------------------------------------------------------------------
# The dependency
# ---------------------------------------------------------------------------


def _build_app(registry: PatientResolverRegistry) -> FastAPI:
    """A minimal app exposing one patient route, wired to *registry*."""
    app = FastAPI()

    @app.get("/patient/me")
    def _me(context: PatientContext = Depends(get_patient_context)) -> dict[str, str]:
        return {
            "patient_id": context.patient_id,
            "schema": context.practice_schema,
            "strength": context.auth_strength.value,
        }

    app.dependency_overrides[get_patient_resolver_registry] = lambda: registry
    return app


@pytest.fixture
def armed(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Capture the search_path / GUC arming without a real database.

    Patched on ``app.auth.patient_context`` rather than ``app.db``: the
    dependency binds these names at import time, so patching the source
    module would leave the already-bound originals in place.
    """
    recorded: dict[str, object] = {}
    sentinel = object()

    monkeypatch.setattr(patient_context_module, "get_db_session", lambda: sentinel)
    monkeypatch.setattr(
        patient_context_module,
        "set_tenant_schema",
        lambda session, schema: recorded.update(session=session, schema=schema),
    )
    monkeypatch.setattr(
        patient_context_module,
        "arm_current_patient_id",
        lambda _session, patient_id: recorded.update(patient_id=patient_id),
    )
    recorded["sentinel"] = sentinel
    return recorded


class TestGetPatientContext:
    def test_resolved_credential_yields_a_patient_principal(self, armed: dict[str, object]) -> None:
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver())
        client = TestClient(_build_app(registry))

        response = client.get("/patient/me", headers={"Authorization": "Bearer good-token"})

        assert response.status_code == 200
        assert response.json() == {
            "patient_id": _PATIENT_ID,
            "schema": _SCHEMA,
            "strength": "stepped_up",
        }

    def test_the_request_session_is_armed_for_the_patient(self, armed: dict[str, object]) -> None:
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver())
        client = TestClient(_build_app(registry))

        client.get("/patient/me", headers={"Authorization": "Bearer good-token"})

        assert armed["schema"] == _SCHEMA
        assert armed["patient_id"] == _PATIENT_ID
        assert armed["session"] is armed["sentinel"]

    def test_unresolvable_credential_is_401(self, armed: dict[str, object]) -> None:
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver())
        client = TestClient(_build_app(registry))

        response = client.get("/patient/me", headers={"Authorization": "Bearer wrong-token"})

        assert response.status_code == 401
        assert response.json()["detail"]["error"]["code"] == "PATIENT_NOT_AUTHENTICATED"

    def test_absent_credential_is_401(self, armed: dict[str, object]) -> None:
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver())
        client = TestClient(_build_app(registry))

        assert client.get("/patient/me").status_code == 401

    def test_a_resolver_that_raises_is_401_not_500(self, armed: dict[str, object]) -> None:
        """A down front door must not become a 500 that names which one broke."""
        registry = PatientResolverRegistry()
        registry.register(_ExplodingResolver())
        client = TestClient(_build_app(registry), raise_server_exceptions=False)

        response = client.get("/patient/me", headers={"Authorization": "Bearer anything"})

        assert response.status_code == 401

    def test_nothing_is_armed_when_the_credential_is_rejected(
        self, armed: dict[str, object]
    ) -> None:
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver())
        client = TestClient(_build_app(registry))

        client.get("/patient/me", headers={"Authorization": "Bearer wrong-token"})

        assert "schema" not in armed
        assert "patient_id" not in armed


class TestHardSeparation:
    """Acceptance criterion 1's separation half, both directions."""

    def test_a_clinician_credential_does_not_satisfy_get_patient_context(
        self, armed: dict[str, object]
    ) -> None:
        """The verified-clinician marker short-circuits before any resolver.

        ``DatabaseSessionMiddleware`` stashes ``verified_identity`` when the
        bearer token verified as a clinician. A resolver that was sloppy
        enough to accept it must never get the chance.
        """
        registry = PatientResolverRegistry()
        # Deliberately permissive: this resolver accepts the clinician's token.
        sloppy = _FakeResolver(accepts="clinician-token")
        registry.register(sloppy)

        app = _build_app(registry)

        @app.middleware("http")
        async def _stash_clinician_identity(request, call_next):  # type: ignore[no-untyped-def]
            request.state.verified_identity = object()
            return await call_next(request)

        client = TestClient(app)
        response = client.get("/patient/me", headers={"Authorization": "Bearer clinician-token"})

        assert response.status_code == 401
        assert sloppy.calls == [], "a clinician credential reached a patient resolver"

    @pytest.mark.parametrize("multi_tenancy", [True, False])
    def test_the_real_middleware_stashes_the_identity_the_guard_reads(
        self, monkeypatch: pytest.MonkeyPatch, multi_tenancy: bool
    ) -> None:
        """The guard above is only worth anything if the wiring feeds it.

        The previous test installs its own middleware to set
        ``verified_identity``, so it proves the guard works *given* that
        state — not that anything in production produces it. The real
        producer is ``DatabaseSessionMiddleware``, and the verify-and-stash
        step used to sit inside its ``if settings.multi_tenancy_enabled:``
        branch. That flag defaults to False, so on a single-tenant install
        — the default, and the shape a self-hosted companion would run —
        nothing ever set the value and the guard was dead code while its
        docstring claimed otherwise.

        Parametrized over both tenancy modes precisely because only one of
        them was broken, and the broken one was the default.
        """

        identity = SimpleNamespace(provider="oidc", email="clinician@example.test", claims={})

        monkeypatch.setattr(
            middleware_module,
            "get_settings",
            lambda: SimpleNamespace(multi_tenancy_enabled=multi_tenancy),
        )
        monkeypatch.setattr(middleware_module, "get_session_factory", lambda: MagicMock)
        monkeypatch.setattr(middleware_module, "set_tenant_schema", lambda *_: None)
        monkeypatch.setattr("app.auth.service.verify_token", lambda _token: identity)
        monkeypatch.setattr(
            middleware_module,
            "_resolve_schema_from_request",
            lambda _request: "practice_abc",
        )

        request = MagicMock()
        request.headers = {"authorization": "Bearer clinician-token"}
        request.state = SimpleNamespace()

        async def _call_next(_request: object) -> object:
            return SimpleNamespace()

        middleware = middleware_module.DatabaseSessionMiddleware(app=MagicMock())
        asyncio.run(middleware.dispatch(request, _call_next))

        assert getattr(request.state, "verified_identity", None) is not None, (
            "DatabaseSessionMiddleware did not stash an identity, so "
            "get_patient_context's clinician guard is dead code "
            f"(multi_tenancy_enabled={multi_tenancy})"
        )

    def test_a_rejected_token_ends_the_clinician_chain_in_401_not_a_fallback(
        self,
    ) -> None:
        """``verify_token`` fails closed when no configured issuer accepts a token.

        Renamed from a "a patient credential does not satisfy a clinician
        dependency" test, which is what it was originally called and is
        NOT what it checks. The token string is decorative — this passes
        identically with any string — so it cannot say anything about
        patient credentials specifically. What it does pin is real and
        worth keeping: the verifier chain ends in a 401 rather than
        falling through to something permissive.

        The actual reverse-direction guarantee — that a patient
        credential is structurally unacceptable to every clinician
        verifier — cannot be tested until a patient credential format
        exists. It is written down as a requirement on resolver authors
        in ``PatientPrincipalResolver``'s docstring instead, and belongs
        in the change that introduces the first real resolver.
        """
        with patch("app.auth.service.firebase_auth.verify_id_token") as mock_verify:
            mock_verify.side_effect = firebase_auth.InvalidIdTokenError("Bad token")

            with pytest.raises(HTTPException) as excinfo:
                verify_token("any-token-no-configured-issuer-accepts")

        assert excinfo.value.status_code == 401

    @pytest.mark.parametrize("scheme", ["Token", "DPoP", "SMART", "anything"])
    def test_the_clinician_stash_ignores_the_scheme_entirely(
        self, monkeypatch: pytest.MonkeyPatch, scheme: str
    ) -> None:
        """The guard has to be at least as scheme-open as the seam it guards.

        ``_credential_from_request`` builds a ``PatientCredential`` out of
        *whatever* scheme it finds and keys the resolver registry on it —
        deliberately, so a future non-bearer front door can register its
        own kind. A stash that only looked at ``bearer`` would therefore
        cover exactly one of those kinds: a clinician's token sent as
        ``Authorization: Token <jwt>`` would skip the stash, leave
        ``verified_identity`` unset, and be handed to whichever resolver
        registers under ``"token"``. That is the same hole the scheme-case
        bug opened, one level up, and it reappears every time the two
        parses disagree — so they must not disagree at all.
        """
        identity = SimpleNamespace(provider="oidc", email="clinician@example.test", claims={})
        monkeypatch.setattr("app.auth.service.verify_token", lambda _token: identity)

        request = MagicMock()
        request.headers = {"authorization": f"{scheme} clinician-token"}
        request.state = SimpleNamespace()

        middleware_module._verify_and_stash_clinician_identity(request)

        assert getattr(request.state, "verified_identity", None) is identity, (
            f"scheme {scheme!r} skipped the stash, so a clinician credential sent "
            f"under it would be offered to every resolver registered for that kind"
        )

    @pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "BeArEr"])
    def test_the_clinician_stash_is_case_insensitive_on_the_scheme(
        self, monkeypatch: pytest.MonkeyPatch, scheme: str
    ) -> None:
        """One lowercased letter used to disarm the clinician guard entirely.

        RFC 7235 makes the auth scheme case-insensitive, and FastAPI's
        ``HTTPBearer`` compares ``scheme.lower() != "bearer"`` — so
        ``Authorization: bearer <token>`` is a perfectly working clinician
        credential on every clinician route. The middleware's stash used
        ``startswith("Bearer ")``, so that header skipped the stash,
        ``verified_identity`` stayed unset, and ``get_patient_context``'s
        guard passed a clinician's token straight to every registered
        patient resolver.
        """
        identity = SimpleNamespace(provider="oidc", email="clinician@example.test", claims={})
        monkeypatch.setattr("app.auth.service.verify_token", lambda _token: identity)

        request = MagicMock()
        request.headers = {"authorization": f"{scheme} clinician-token"}
        request.state = SimpleNamespace()

        middleware_module._verify_and_stash_clinician_identity(request)

        assert getattr(request.state, "verified_identity", None) is identity, (
            f"scheme {scheme!r} skipped the stash, which disarms the clinician guard"
        )

    def test_a_lowercase_clinician_token_still_cannot_reach_a_resolver(
        self, armed: dict[str, object], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end: the stash and the guard together, on the bypass header."""
        registry = PatientResolverRegistry()
        sloppy = _FakeResolver(accepts="clinician-token")
        registry.register(sloppy)

        identity = SimpleNamespace(provider="oidc", email="clinician@example.test", claims={})
        monkeypatch.setattr("app.auth.service.verify_token", lambda _token: identity)

        app = _build_app(registry)

        @app.middleware("http")
        async def _run_the_real_stash(request, call_next):  # type: ignore[no-untyped-def]
            middleware_module._verify_and_stash_clinician_identity(request)
            return await call_next(request)

        client = TestClient(app)
        response = client.get("/patient/me", headers={"Authorization": "bearer clinician-token"})

        assert response.status_code == 401
        assert sloppy.calls == [], "a lowercase-scheme clinician token reached a resolver"

    def test_patient_context_is_not_a_tenant_context(self) -> None:
        """No shared base class: a patient must not be duck-type substitutable."""
        assert not issubclass(PatientContext, TenantContext)
        assert not issubclass(TenantContext, PatientContext)
        assert not hasattr(
            PatientContext(
                patient_id=_PATIENT_ID,
                practice_schema=_SCHEMA,
                credential_kind="bearer",
                auth_strength=AuthStrength.STEPPED_UP,
            ),
            "user_id",
        )


class TestUnauthenticatedResponseIsUniform:
    def test_every_failure_mode_returns_the_same_body(self, armed: dict[str, object]) -> None:
        """An unauthenticated probe must not learn which door it got wrong."""
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver())
        client = TestClient(_build_app(registry), raise_server_exceptions=False)

        bodies = {
            client.get("/patient/me").json()["detail"]["error"]["code"],
            client.get("/patient/me", headers={"Authorization": "Bearer nope"}).json()["detail"][
                "error"
            ]["code"],
            client.get("/patient/me", headers={"Authorization": "Widget nope"}).json()["detail"][
                "error"
            ]["code"],
        }
        assert bodies == {"PATIENT_NOT_AUTHENTICATED"}


class TestAuthStrength:
    def test_strength_is_carried_through_not_enforced(self, armed: dict[str, object]) -> None:
        """Single-factor resolves; routes decide what strength they need."""
        registry = PatientResolverRegistry()
        registry.register(_FakeResolver(strength=AuthStrength.SINGLE_FACTOR))
        client = TestClient(_build_app(registry))

        response = client.get("/patient/me", headers={"Authorization": "Bearer good-token"})

        assert response.status_code == 200
        assert response.json()["strength"] == "single_factor"


class TestDependencyRaisesNotReturnsNone:
    def test_the_dependency_never_hands_a_route_a_none_principal(self) -> None:
        """A route body must be unreachable without a resolved patient."""
        registry = PatientResolverRegistry()
        request = _StubRequest({"authorization": "Bearer nope"})

        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(get_patient_context(request, registry))  # type: ignore[arg-type]

        assert excinfo.value.status_code == 401
