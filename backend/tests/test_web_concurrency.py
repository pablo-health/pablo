# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""More than one web worker (PABLO-5zm8).

Two claims, each easy to break without noticing:

* Every worker loads the runtime extension. uvicorn starts each worker as a
  fresh process that re-executes ``entrypoint.py``; the extension import has
  to sit where that re-execution reaches it, and ``uvicorn.run`` has to sit
  where it does not. Only a real subprocess shows either.
* The in-process background loops refuse to run in more than one worker,
  because nothing stops two copies of the claims pipeline submitting at once.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
from app.main import _refuse_loops_in_multiple_workers
from app.settings import Settings

_BACKEND = Path(__file__).resolve().parents[1]
_ENTRYPOINT = _BACKEND / "entrypoint.py"


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "pablo_edition": "core",
        "calendar_auto_sync_enabled": False,
        "claims_pipeline_enabled": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_one_worker_runs_the_loops_as_before() -> None:
    _refuse_loops_in_multiple_workers(
        _settings(web_concurrency=1, calendar_auto_sync_enabled=True, claims_pipeline_enabled=True)
    )


def test_several_workers_with_the_loops_off_start() -> None:
    _refuse_loops_in_multiple_workers(_settings(web_concurrency=2))


@pytest.mark.parametrize(
    ("loop", "env_name"),
    [
        ("calendar_auto_sync_enabled", "CALENDAR_AUTO_SYNC_ENABLED"),
        ("claims_pipeline_enabled", "CLAIMS_PIPELINE_ENABLED"),
    ],
)
def test_several_workers_with_a_loop_on_refuse_to_start(loop: str, env_name: str) -> None:
    with pytest.raises(RuntimeError, match=env_name):
        _refuse_loops_in_multiple_workers(_settings(web_concurrency=2, **{loop: True}))


def test_a_hosted_edition_does_not_run_the_loops_so_is_not_refused() -> None:
    _refuse_loops_in_multiple_workers(
        _settings(pablo_edition="solo", web_concurrency=2, claims_pipeline_enabled=True)
    )


def test_zero_workers_is_not_a_setting() -> None:
    with pytest.raises(ValueError, match="web_concurrency"):
        _settings(web_concurrency=0)


def test_every_worker_loads_the_runtime_extension_and_starts_the_app(tmp_path: Path) -> None:
    marker = tmp_path / "loaded.txt"
    log = tmp_path / "server.log"
    (tmp_path / "pablo_test_extension.py").write_text(
        f"import os\nwith open({str(marker)!r}, 'a') as f:\n    f.write(f'{{os.getpid()}}\\n')\n"
    )
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(tmp_path), str(_BACKEND.parent), str(_BACKEND)]),
        "PABLO_RUNTIME_EXTENSION": "pablo_test_extension",
        "WEB_CONCURRENCY": "2",
        "PORT": str(port),
        "CALENDAR_AUTO_SYNC_ENABLED": "false",
        "CLAIMS_PIPELINE_ENABLED": "false",
    }
    env.pop("NEW_RELIC_LICENSE_KEY", None)
    env.pop("SENTRY_DSN", None)
    with log.open("wb") as out:
        proc = subprocess.Popen(  # noqa: S603 — fixed argv, our own interpreter
            [sys.executable, str(_ENTRYPOINT)],
            env=env,
            cwd=tmp_path,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline and proc.poll() is None:
                if _startups(log) >= 2:
                    break
                time.sleep(0.5)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    output = log.read_text(errors="replace")
    # A worker that re-executed uvicorn.run would die starting a nested pool
    # ("...before the current process has finished its bootstrapping phase"),
    # be respawned, and die again: no worker ever finishes starting the app.
    assert "bootstrapping phase" not in output, output[-4000:]
    assert _startups(log) >= 2, output[-4000:]
    # The supervisor and both workers each imported the extension.
    assert len(set(marker.read_text().split())) >= 3, output[-4000:]


def _startups(log: Path) -> int:
    return log.read_text(errors="replace").count("Application startup complete")
