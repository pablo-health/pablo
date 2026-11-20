# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""``pablo-name release`` against a stub names service and metadata server.

``deploy/vm/bin/pablo-name`` runs as written, with only its fixed paths (the
config directory, the teardown marker, the metadata server) pointed into a
temporary directory. A local HTTP server plays both the metadata server and
the names service, and records every request, so each test can say which
calls reached the service.

Two paths: ``release --teardown`` gives up the name the VM uses and stops the
weekly renewal; plain ``release`` still refuses that name.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "deploy" / "vm" / "bin" / "pablo-name"
NAME = "yourpractice"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None or shutil.which("curl") is None,
    reason="needs bash and curl",
)


@dataclass
class Stub:
    """What the service answers a DELETE with, and every request it saw."""

    delete_status: int = 204
    requests: list[tuple[str, str]] = field(default_factory=list)


def _handler(stub: Stub) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def _answer(self, status: int, body: bytes = b"", kind: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.startswith("/computeMetadata/"):
                self._answer(200, b"identity-token", "text/plain")
                return
            stub.requests.append(("GET", self.path))
            self._answer(200, b'{"available": false, "reason": "taken"}')

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            stub.requests.append(("POST", self.path))
            self._answer(
                200,
                b'{"hostname": "yourpractice.pablo.clinic", "address": "203.0.113.7",'
                b' "outcome": "renewed"}',
            )

        def do_DELETE(self) -> None:
            stub.requests.append(("DELETE", self.path))
            if self.headers.get("Authorization") != "Bearer identity-token":
                self._answer(401)
            elif stub.delete_status == 204:
                self._answer(204)
            else:
                self._answer(stub.delete_status, b'{"detail": "refused"}')

    return Handler


@dataclass
class VM:
    stub: Stub
    script: Path
    marker: Path
    logged: Path
    env: dict[str, str]

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 — fixed argv, our own script
            ["bash", str(self.script), *args],  # noqa: S607
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )


@pytest.fixture
def vm(tmp_path: Path) -> Iterator[VM]:
    stub = Stub()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(stub))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    config = tmp_path / "etc"
    config.mkdir()
    (config / "config.env").write_text(f"HOSTED_NAME={NAME}\nNAMES_ENDPOINT={base}\n")
    marker = tmp_path / "var" / "hosted-name-released"

    text = SCRIPT.read_text()
    for old, new in (
        ("readonly CONFIG_DIR=/etc/pablo", f"readonly CONFIG_DIR={config}"),
        ("readonly RELEASED=/var/lib/pablo/hosted-name-released", f"readonly RELEASED={marker}"),
        (
            "readonly METADATA=http://metadata.google.internal/computeMetadata/v1/instance",
            f"readonly METADATA={base}/computeMetadata/v1/instance",
        ),
    ):
        assert old in text, f"pablo-name no longer has: {old}"
        text = text.replace(old, new)
    script = tmp_path / "pablo-name"
    script.write_text(text)

    # logger(1) writes to the journal; here it appends to a file instead.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    logged = tmp_path / "logged"
    (bin_dir / "logger").write_text(f'#!/bin/sh\ncat >> "{logged}"\n')
    (bin_dir / "logger").chmod(0o755)

    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    try:
        yield VM(stub=stub, script=script, marker=marker, logged=logged, env=env)
    finally:
        server.shutdown()
        server.server_close()


def test_teardown_releases_the_current_name_and_stops_the_renewal(vm: VM) -> None:
    result = vm.run("release", "--teardown")

    assert result.returncode == 0, result.stderr
    assert vm.stub.requests == [("DELETE", f"/v1/names/{NAME}")]
    assert f"released {NAME}.pablo.clinic" in result.stderr
    assert vm.marker.read_text().strip() == NAME

    renewal = vm.run("renew")

    assert renewal.returncode == 0, renewal.stderr
    assert vm.stub.requests == [("DELETE", f"/v1/names/{NAME}")]
    assert '"status":"released"' in vm.logged.read_text()


def test_teardown_accepts_the_current_name_spelled_out(vm: VM) -> None:
    result = vm.run("release", "--teardown", NAME)

    assert result.returncode == 0, result.stderr
    assert vm.stub.requests == [("DELETE", f"/v1/names/{NAME}")]


def test_teardown_refuses_a_name_this_vm_does_not_use(vm: VM) -> None:
    result = vm.run("release", "--teardown", "oldpractice")

    assert result.returncode == 1
    assert vm.stub.requests == []
    assert not vm.marker.exists()


def test_failed_teardown_keeps_the_renewal(vm: VM) -> None:
    vm.stub.delete_status = 403

    result = vm.run("release", "--teardown")

    assert result.returncode == 1
    assert "belongs to another project" in result.stderr
    assert not vm.marker.exists()
    assert vm.run("renew").returncode == 0
    assert vm.stub.requests[-1] == ("POST", "/v1/names")


def test_a_claim_after_teardown_resumes_the_renewal(vm: VM) -> None:
    assert vm.run("release", "--teardown").returncode == 0

    assert vm.run("claim").returncode == 0
    assert not vm.marker.exists()
    assert vm.run("renew").returncode == 0
    assert vm.stub.requests[-1] == ("POST", "/v1/names")


def test_plain_release_still_refuses_the_current_name(vm: VM) -> None:
    result = vm.run("release", NAME)

    assert result.returncode == 1
    assert "pablo-name release --teardown" in result.stderr
    assert vm.stub.requests == []
    assert not vm.marker.exists()


def test_plain_release_gives_up_another_name_and_keeps_the_renewal(vm: VM) -> None:
    result = vm.run("release", "oldpractice")

    assert result.returncode == 0, result.stderr
    assert vm.stub.requests == [("DELETE", "/v1/names/oldpractice")]
    assert not vm.marker.exists()
    assert vm.run("renew").returncode == 0
    assert vm.stub.requests[-1] == ("POST", "/v1/names")
