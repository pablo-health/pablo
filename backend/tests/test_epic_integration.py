# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Unit tests for the standalone Epic / MyChart SMART on FHIR puller."""

import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from integrations.epic.cli import _resolve_settings, build_parser, main
from integrations.epic.config import EpicSettings
from integrations.epic.errors import EpicConfigError
from integrations.epic.exporter import export_patient_data
from integrations.epic.fhir_client import FhirClient, _next_link
from integrations.epic.smart_auth import (
    StandaloneLaunchFlow,
    discover_smart_configuration,
    generate_pkce_pair,
)

FHIR_BASE = "https://fhir.example.org/api/FHIR/R4"


def _settings(**overrides: object) -> EpicSettings:
    base: dict[str, object] = {"client_id": "test-client", "fhir_base_url": FHIR_BASE}
    base.update(overrides)
    return EpicSettings().model_copy(update=base)


def test_generate_pkce_pair_is_s256() -> None:
    verifier, challenge = generate_pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
    assert challenge == expected.rstrip(b"=").decode("ascii")
    assert "=" not in challenge  # base64url, padding stripped


def test_build_authorize_url_includes_pkce_and_aud() -> None:
    settings = _settings(redirect_port=9000)
    with httpx.Client() as client:
        flow = StandaloneLaunchFlow(settings, client)
        url = flow._build_authorize_url("https://auth.example/authorize", "CHAL", "STATE")

    query = parse_qs(urlparse(url).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["test-client"]
    assert query["redirect_uri"] == ["http://127.0.0.1:9000/callback"]
    assert query["aud"] == [FHIR_BASE]
    assert query["code_challenge"] == ["CHAL"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"] == ["STATE"]


def test_discover_smart_configuration() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/.well-known/smart-configuration")
        return httpx.Response(
            200,
            json={
                "authorization_endpoint": "https://auth.example/authorize",
                "token_endpoint": "https://auth.example/token",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        config = discover_smart_configuration(FHIR_BASE, client)

    assert config.authorization_endpoint == "https://auth.example/authorize"
    assert config.token_endpoint == "https://auth.example/token"


def test_exchange_code_parses_patient_context() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = parse_qs(request.content.decode())
        assert body["grant_type"] == ["authorization_code"]
        assert body["code_verifier"] == ["verifier-123"]
        assert body["code"] == ["auth-code"]
        return httpx.Response(
            200,
            json={
                "access_token": "tok",
                "patient": "patient-42",
                "scope": "patient/Patient.read",
                "expires_in": 3600,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        flow = StandaloneLaunchFlow(_settings(), client)
        token = flow._exchange_code("https://auth.example/token", "auth-code", "verifier-123")

    assert token.access_token == "tok"
    assert token.patient_id == "patient-42"
    assert token.expires_in == 3600


def test_fhir_client_search_follows_next_links() -> None:
    page_two = f"{FHIR_BASE}/Condition?_getpages=cursor"

    def handler(request: httpx.Request) -> httpx.Response:
        if "_getpages" in request.url.query.decode():
            return httpx.Response(200, json={"resourceType": "Bundle", "entry": [{"id": "c2"}]})
        assert request.url.params["patient"] == "p1"
        return httpx.Response(
            200,
            json={
                "resourceType": "Bundle",
                "entry": [{"id": "c1"}],
                "link": [{"relation": "next", "url": page_two}],
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fhir = FhirClient(FHIR_BASE, "tok", client)
        bundle = fhir.search("Condition", {"patient": "p1"})

    assert bundle["total"] == 2
    assert [entry["id"] for entry in bundle["entry"]] == ["c1", "c2"]


def test_next_link_returns_none_without_next() -> None:
    assert _next_link({"link": [{"relation": "self", "url": "x"}]}) is None


class _FakeFhirClient:
    """Stub FhirClient that returns canned resources without network I/O."""

    def read(self, resource_type: str, resource_id: str) -> dict[str, object]:
        return {"resourceType": resource_type, "id": resource_id}

    def search(self, resource_type: str, params: dict[str, str]) -> dict[str, object]:
        return {"resourceType": "Bundle", "type": "searchset", "total": 1, "entry": [{}]}


def test_export_patient_data_writes_files(tmp_path: Path) -> None:
    summary = export_patient_data(_FakeFhirClient(), "patient-7", tmp_path)  # type: ignore[arg-type]  # stub duck-types FhirClient in tests

    assert summary.output_dir.parent == tmp_path
    patient = json.loads((summary.output_dir / "Patient.json").read_text())
    assert patient["id"] == "patient-7"

    metadata = json.loads((summary.output_dir / "_export_metadata.json").read_text())
    assert metadata["patient_id"] == "patient-7"
    assert summary.counts["Patient"] == 1
    assert (summary.output_dir / "Condition.json").exists()
    assert (summary.output_dir / "Observation_laboratory.json").exists()


def test_resolve_settings_applies_cli_overrides() -> None:
    args = build_parser().parse_args(["--client-id", "cli-id", "--port", "9999"])
    settings = _resolve_settings(args)
    assert settings.client_id == "cli-id"
    assert settings.redirect_port == 9999


def test_main_requires_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EPIC_CLIENT_ID", raising=False)
    with pytest.raises(EpicConfigError):
        main([])
