# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Tests for GET /api/users/me/devices (enrolled companion list)."""

from typing import Any

import pytest
from app.main import app
from app.models.companion_device import CompanionDevice
from app.repositories.companion_device import InMemoryCompanionDeviceRepository
from app.routes.users import get_companion_device_service_dep
from app.services.companion_device_service import CompanionDeviceService
from app.utcnow import utc_now
from fastapi.testclient import TestClient


@pytest.fixture
def device_repo() -> InMemoryCompanionDeviceRepository:
    return InMemoryCompanionDeviceRepository()


@pytest.fixture
def devices_client(client: TestClient, device_repo: InMemoryCompanionDeviceRepository) -> Any:
    """The shared client, with the companion-device service backed by an
    in-memory repo so /me/devices has deterministic data.

    The shared ``client`` fixture clears dependency overrides on teardown.
    """
    service = CompanionDeviceService(device_repo)
    app.dependency_overrides[get_companion_device_service_dep] = lambda: service
    return client


def _enroll(
    repo: InMemoryCompanionDeviceRepository,
    *,
    install_id: str,
    user_id: str,
    platform: str = "mac",
    jkt: str = "a3f9c2e1d5b7aaaaaaaa",
    revoked: bool = False,
) -> None:
    now = utc_now()
    repo.upsert(
        CompanionDevice(
            install_id=install_id,
            user_id=user_id,
            device_public_key_jwk={"kty": "EC", "crv": "P-256", "x": "x", "y": "y"},
            jkt=jkt,
            key_storage="hardware",
            platform=platform,  # type: ignore[arg-type]
            os_version="14.5",
            hostname_hash="deadbeef",
            enrolled_at=now,
            last_seen=now,
            revoked_at=now if revoked else None,
        )
    )


def test_devices_empty_returns_empty_array(devices_client: TestClient) -> None:
    resp = devices_client.get("/api/users/me/devices")
    assert resp.status_code == 200
    assert resp.json() == []


def test_devices_nonempty_returns_caller_devices(
    devices_client: TestClient, device_repo: InMemoryCompanionDeviceRepository
) -> None:
    # mock_user.id is "test-user-123" (see conftest).
    _enroll(device_repo, install_id="install-1", user_id="test-user-123")
    resp = devices_client.get("/api/users/me/devices")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    item = body[0]
    assert item["install_id"] == "install-1"
    assert item["platform"] == "mac"
    assert item["os_version"] == "14.5"
    # jkt_fingerprint is the first 12 chars of the stored thumbprint.
    assert item["jkt_fingerprint"] == "a3f9c2e1d5b7"
    # No PHI / no secrets leaked.
    assert "hostname_hash" not in item
    assert "device_public_key_jwk" not in item
    assert item["enrolled_at"].endswith("Z")


def test_devices_excludes_other_users_and_revoked(
    devices_client: TestClient, device_repo: InMemoryCompanionDeviceRepository
) -> None:
    _enroll(device_repo, install_id="mine", user_id="test-user-123")
    _enroll(device_repo, install_id="theirs", user_id="other-user")
    _enroll(device_repo, install_id="revoked", user_id="test-user-123", revoked=True)
    resp = devices_client.get("/api/users/me/devices")
    assert resp.status_code == 200
    install_ids = {d["install_id"] for d in resp.json()}
    assert install_ids == {"mine"}
