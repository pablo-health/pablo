# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The waiting-room webhook: off by default, verified, and safe to redeliver."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from app.routes import telehealth_webhooks
from app.routes.telehealth_webhooks import TELEHEALTH_WEBHOOK_PATH
from app.settings import get_settings
from fastapi import FastAPI
from fastapi.testclient import TestClient

SECRET = "a-shared-secret"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("TELEHEALTH_DOXY_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(telehealth_webhooks.router)
    yield TestClient(app)
    get_settings.cache_clear()


@pytest.fixture
def disabled_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("TELEHEALTH_DOXY_WEBHOOK_SECRET", raising=False)
    get_settings.cache_clear()
    app = FastAPI()
    app.include_router(telehealth_webhooks.router)
    yield TestClient(app)
    get_settings.cache_clear()


def an_event(**overrides: Any) -> dict[str, Any]:
    event = {"event_id": "evt-1", "event_type": "check_in", "pid": "handle-1"}
    event.update(overrides)
    return event


class TestItIsOffUntilAPracticeTurnsItOn:
    def test_no_secret_means_the_route_is_not_there(self, disabled_client: TestClient) -> None:
        """404 rather than 401: a 401 would confirm the endpoint exists."""
        response = disabled_client.post(TELEHEALTH_WEBHOOK_PATH, json=an_event())
        assert response.status_code == 404


class TestVerification:
    def test_a_missing_secret_is_refused(self, client: TestClient) -> None:
        assert client.post(TELEHEALTH_WEBHOOK_PATH, json=an_event()).status_code == 401

    def test_a_wrong_secret_is_refused(self, client: TestClient) -> None:
        response = client.post(
            TELEHEALTH_WEBHOOK_PATH,
            json=an_event(),
            headers={"X-Pablo-Room-Secret": "not-it"},
        )
        assert response.status_code == 401

    def test_the_body_is_not_parsed_before_the_secret_is_checked(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        recorded: list[str] = []
        monkeypatch.setattr(
            telehealth_webhooks,
            "_record_event",
            lambda handle, _column: recorded.append(handle) or "applied",
        )
        client.post(
            TELEHEALTH_WEBHOOK_PATH,
            content=b"{not json",
            headers={"X-Pablo-Room-Secret": "not-it"},
        )
        assert recorded == []

    def test_the_comparison_is_constant_time(self) -> None:
        """Read from the source, because a timing leak is invisible in a response."""
        source = Path(telehealth_webhooks.__file__).read_text()
        assert "hmac.compare_digest" in source


class TestMalformedDeliveries:
    def test_a_body_that_is_not_json_is_400(self, client: TestClient) -> None:
        response = client.post(
            TELEHEALTH_WEBHOOK_PATH,
            content=b"{not json",
            headers={"X-Pablo-Room-Secret": SECRET},
        )
        assert response.status_code == 400

    def test_a_body_that_is_not_an_object_is_400(self, client: TestClient) -> None:
        response = client.post(
            TELEHEALTH_WEBHOOK_PATH, json=[1, 2], headers={"X-Pablo-Room-Secret": SECRET}
        )
        assert response.status_code == 400

    @pytest.mark.parametrize("missing", ["event_id", "pid"])
    def test_a_delivery_that_names_nothing_is_400(self, client: TestClient, missing: str) -> None:
        event = an_event()
        event.pop(missing)
        response = client.post(
            TELEHEALTH_WEBHOOK_PATH, json=event, headers={"X-Pablo-Room-Secret": SECRET}
        )
        assert response.status_code == 400


class TestWhatItRecords:
    @pytest.mark.parametrize(
        ("event_type", "column"),
        [
            ("check_in", "telehealth_checked_in_at"),
            ("call_start", "telehealth_started_at"),
            ("call_end", "telehealth_ended_at"),
        ],
    )
    def test_each_event_kind_writes_its_own_column(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        event_type: str,
        column: str,
    ) -> None:
        seen: list[tuple[str, str]] = []
        monkeypatch.setattr(
            telehealth_webhooks,
            "_record_event",
            lambda handle, col: seen.append((handle, col)) or "applied",
        )

        response = client.post(
            TELEHEALTH_WEBHOOK_PATH,
            json=an_event(event_type=event_type),
            headers={"X-Pablo-Room-Secret": SECRET},
        )

        assert response.status_code == 200
        assert seen == [("handle-1", column)]

    def test_a_redelivery_changes_nothing_and_still_succeeds(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The column already being set IS the dedupe; there is no second ledger."""
        monkeypatch.setattr(telehealth_webhooks, "_record_event", lambda *_a: "deduped")

        response = client.post(
            TELEHEALTH_WEBHOOK_PATH,
            json=an_event(),
            headers={"X-Pablo-Room-Secret": SECRET},
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "deduped"

    def test_an_event_kind_we_do_not_act_on_is_acknowledged(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A retry loop over an unhandleable event gets the destination disabled."""
        called: list[str] = []
        monkeypatch.setattr(
            telehealth_webhooks,
            "_record_event",
            lambda handle, _col: called.append(handle) or "applied",
        )

        response = client.post(
            TELEHEALTH_WEBHOOK_PATH,
            json=an_event(event_type="room_renamed"),
            headers={"X-Pablo-Room-Secret": SECRET},
        )

        assert response.status_code == 200
        assert called == []

    def test_a_handle_nobody_recognises_is_ordinary_traffic(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Rooms are used for things that are not Pablo appointments."""
        monkeypatch.setattr(telehealth_webhooks, "_record_event", lambda *_a: "unmatched")

        response = client.post(
            TELEHEALTH_WEBHOOK_PATH,
            json=an_event(),
            headers={"X-Pablo-Room-Secret": SECRET},
        )

        assert response.status_code == 200
        assert response.json()["outcome"] == "unmatched"


class TestLogs:
    def test_the_handle_itself_never_reaches_a_log_line(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(telehealth_webhooks, "_record_event", lambda *_a: "applied")
        with caplog.at_level("INFO"):
            client.post(
                TELEHEALTH_WEBHOOK_PATH,
                json=an_event(pid="a-very-distinctive-handle"),
                headers={"X-Pablo-Room-Secret": SECRET},
            )
        assert "a-very-distinctive-handle" not in caplog.text
