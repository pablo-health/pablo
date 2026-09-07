# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Where the clearinghouse adapter sends its calls.

Two things worth holding still. First, the four defaults, written out here
as literals: a deployment that configures nothing files real claims against
these hosts, so a typo introduced while refactoring them would quietly
redirect production traffic and nothing else in the tree would notice.

Second, that a configured base URL is actually honoured — proved by standing
a server up on loopback and watching the requests arrive rather than by
asserting on a string. That is the property the end-to-end harness's
stand-in clearinghouse depends on, and the reason a mock transport is not
enough here: the harness fails at the socket, not at the URL.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any, ClassVar

import pytest
from app.claims.credentials import ClearinghouseCredentials
from app.claims.stedi import (
    CORE_API_BASE,
    DEFAULT_API_BASES,
    ENROLLMENTS_API_BASE,
    HEALTHCARE_API_BASE,
    PAYERS_API_BASE,
    ApiBases,
    StediClearinghouseClient,
)
from app.models.claims_transport import EnrollmentFilters

if TYPE_CHECKING:
    from collections.abc import Iterator

#: Whatever the adapter asks this server for, it gets a body that parses.
#: Enough for the question under test, which is where the request landed.
_RESPONSES: dict[str, dict[str, Any]] = {
    "/2024-04-01/payers/search": {"items": []},
    "/2024-04-01/change/medicalnetwork/reports/v2/txn-1/277": {},
    "/2023-08-01/transactions/txn-1": {
        "transactionId": "txn-1",
        "direction": "INBOUND",
        "processedAt": "2026-09-06T00:00:00Z",
    },
    "/2024-09-01/enrollments": {"items": []},
}


class TestDefaultBases:
    """The vendor's own four hosts, pinned."""

    def test_the_defaults_are_the_vendors_hosts(self) -> None:
        assert HEALTHCARE_API_BASE == "https://healthcare.us.stedi.com/2024-04-01"
        assert PAYERS_API_BASE == "https://payers.us.stedi.com/2024-04-01"
        assert CORE_API_BASE == "https://core.us.stedi.com/2023-08-01"
        assert ENROLLMENTS_API_BASE == "https://enrollments.us.stedi.com/2024-09-01"

    def test_no_configured_base_url_resolves_to_the_defaults(self) -> None:
        assert ApiBases.resolve(None) == DEFAULT_API_BASES
        assert ApiBases.resolve("") == DEFAULT_API_BASES

    def test_an_origin_keeps_each_apis_version_path(self) -> None:
        bases = ApiBases.resolve("http://fake-clearinghouse:8080/")

        assert bases == ApiBases(
            healthcare="http://fake-clearinghouse:8080/2024-04-01",
            payers="http://fake-clearinghouse:8080/2024-04-01",
            core="http://fake-clearinghouse:8080/2023-08-01",
            enrollments="http://fake-clearinghouse:8080/2024-09-01",
        )


class _Recorder(BaseHTTPRequestHandler):
    paths: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        type(self).paths.append(path)
        body = json.dumps(_RESPONSES.get(path, {})).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """Silence the handler's stderr logging."""


@pytest.fixture
def recording_server() -> Iterator[tuple[str, list[str]]]:
    """A loopback server, and the list of paths it has been asked for."""
    _Recorder.paths = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        yield f"http://127.0.0.1:{port}", _Recorder.paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class TestConfiguredBaseUrl:
    def test_every_host_is_answered_by_the_configured_origin(
        self, recording_server: tuple[str, list[str]]
    ) -> None:
        origin, received = recording_server
        credentials = ClearinghouseCredentials(
            api_key="test_placeholder", mode="test", base_url=origin
        )
        client = StediClearinghouseClient(credentials)

        client.search_payers("aetna")
        client.get_claim_acknowledgment("txn-1")
        client.get_transaction("txn-1")
        client.list_enrollments(EnrollmentFilters())

        assert received == [
            "/2024-04-01/payers/search",
            "/2024-04-01/change/medicalnetwork/reports/v2/txn-1/277",
            "/2023-08-01/transactions/txn-1",
            "/2024-09-01/enrollments",
        ]
