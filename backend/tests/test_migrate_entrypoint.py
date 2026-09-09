# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The migrate job's single-practice hook (PABLO-2g6.4).

``backend/bin/migrate.py`` runs the single-practice migration after a successful
``alembic upgrade head``, so no deployment needs a human to remember an ordering.
The interesting behaviour is all in *when it declines to run*:

* not after a ``downgrade`` — a schema rename is not something to do on the way
  back down;
* not after a failed upgrade — the practice migration assumes head;
* not on an already-migrated deployment, which includes every fresh install.

And when the pre-flight refuses, the job must fail rather than proceed: failing
here is what stops the rollout, with nothing deployed.

The alembic call itself is not exercised — that is alembic's job, and standing
up a real chain to prove `_is_upgrade("downgrade") is False` would be a slower
test of a smaller claim.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

_MIGRATE_PATH = Path(__file__).resolve().parents[1] / "bin" / "migrate.py"


def _load() -> ModuleType:
    """Import the entrypoint by path — ``bin/`` is not an importable package."""
    spec = importlib.util.spec_from_file_location("_migrate_entrypoint", _MIGRATE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


migrate_entrypoint = _load()


# ---------------------------------------------------------------------------
# Which invocations should trigger it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["upgrade", "head"], True),
        (["upgrade", "+1"], True),
        ([], False),  # the caller substitutes the default before asking
        (["downgrade", "-1"], False),
        (["current"], False),
        (["history"], False),
        (["stamp", "head"], False),
        # A flag's VALUE is a non-flag token sitting before the subcommand, so a
        # scan for "first non-flag token" reads `foo=bar` as the command and
        # gets this wrong. Both directions, because getting it wrong the other
        # way would run a rename on a downgrade.
        (["-x", "foo=bar", "upgrade", "head"], True),
        (["-x", "foo=bar", "downgrade", "-1"], False),
        (["-c", "alembic.ini", "upgrade", "head"], True),
        (["-n", "upgrade", "downgrade", "-1"], False),
    ],
)
def test_only_an_upgrade_triggers_the_practice_migration(argv: list[str], expected: bool) -> None:
    assert migrate_entrypoint._is_upgrade(argv) is expected


# ---------------------------------------------------------------------------
# What it does once triggered
# ---------------------------------------------------------------------------


class _Recorder:
    """Stands in for app.db.single_practice_migration."""

    def __init__(self, *, migrated: bool, raises: Exception | None = None) -> None:
        self.migrated = migrated
        self.raises = raises
        self.migrate_calls = 0

    def is_migrated(self, _engine: Any) -> bool:
        return self.migrated

    def migrate(self, _engine: Any, **_kw: Any) -> list[Any]:
        self.migrate_calls += 1
        if self.raises is not None:
            raise self.raises
        return []

    def preflight(self, _engine: Any, *_a: Any) -> list[Any]:
        return []

    def format_report(self, _counts: Any) -> str:
        return "(report)"


class _PreflightError(RuntimeError):
    pass


def _install(monkeypatch: pytest.MonkeyPatch, recorder: _Recorder) -> None:
    """Point the entrypoint's lazy imports at the recorder."""
    db_mod = ModuleType("app.db")
    db_mod.get_engine = MagicMock  # type: ignore[attr-defined]

    spm = ModuleType("app.db.single_practice_migration")
    spm.PreflightError = _PreflightError  # type: ignore[attr-defined]
    spm.is_migrated = recorder.is_migrated  # type: ignore[attr-defined]
    spm.migrate = recorder.migrate  # type: ignore[attr-defined]
    spm.preflight = recorder.preflight  # type: ignore[attr-defined]
    spm.format_report = recorder.format_report  # type: ignore[attr-defined]

    app_mod = ModuleType("app")
    monkeypatch.setitem(sys.modules, "app", app_mod)
    monkeypatch.setitem(sys.modules, "app.db", db_mod)
    monkeypatch.setitem(sys.modules, "app.db.single_practice_migration", spm)


def test_an_already_migrated_deployment_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every fresh install lands here — it boots straight onto its own practice —
    so this is the common path and must be silent and cheap."""
    recorder = _Recorder(migrated=True)
    _install(monkeypatch, recorder)

    assert migrate_entrypoint._run_single_practice_migration() == 0
    assert recorder.migrate_calls == 0


def test_an_unmigrated_deployment_is_migrated(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder(migrated=False)
    _install(monkeypatch, recorder)

    assert migrate_entrypoint._run_single_practice_migration() == 0
    assert recorder.migrate_calls == 1


def test_a_refusing_preflight_fails_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """The job failing is what stops the rollout.

    Returning 0 here would deploy a revision that cannot serve, which is the
    failure this whole mechanism exists to prevent — and it would do it while
    reporting success.
    """
    recorder = _Recorder(migrated=False, raises=_PreflightError("rows would vanish"))
    _install(monkeypatch, recorder)

    assert migrate_entrypoint._run_single_practice_migration() == 1
    assert recorder.migrate_calls == 1


def test_it_never_forces_past_the_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unattended job must not decide for itself that losing rows is fine."""
    seen: dict[str, Any] = {}

    class _ForceWatcher(_Recorder):
        def migrate(self, _engine: Any, **kw: Any) -> list[Any]:
            seen.update(kw)
            return super().migrate(_engine, **kw)

    _install(monkeypatch, _ForceWatcher(migrated=False))

    migrate_entrypoint._run_single_practice_migration()

    assert seen.get("force") in (None, False), (
        f"the migrate job passed force={seen.get('force')!r} — an unattended job "
        "must not override a pre-flight refusal"
    )


class TestTheJobCanStillSpeakAfterAlembicConfiguresLogging:
    """The refusal has to reach the log, or the deploy fails saying nothing.

    On 2026-09-09 it did exactly that: the OSS migrate job ran the whole
    alembic chain, then exited 1 with twenty lines of alembic INFO and not one
    word about why. ``logging.config.fileConfig`` defaults to
    ``disable_existing_loggers=True``, so alembic's own logging setup switched
    off the logger ``bin/migrate.py`` had created at import — the only logger
    the single-practice migration writes its pre-flight report and its refusal
    to. Two hours of the failure being invisible, for one default.
    """

    def test_env_py_keeps_existing_loggers_alive(self) -> None:
        """Asserted against the call itself, because the symptom is silence.

        A behavioural test here would have to run a real alembic upgrade and
        then prove a negative about output, which is exactly the shape of test
        that passes for the wrong reason. The argument IS the behaviour.
        """
        import ast  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415

        env_py = _Path(__file__).resolve().parents[1] / "alembic" / "env.py"
        tree = ast.parse(env_py.read_text(encoding="utf-8"))

        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "fileConfig"
        ]
        assert calls, "alembic/env.py no longer calls fileConfig — has logging moved?"

        for call in calls:
            kwargs = {kw.arg: kw.value for kw in call.keywords}
            assert "disable_existing_loggers" in kwargs, (
                "alembic/env.py calls fileConfig without disable_existing_loggers. "
                "The default is True, which switches off every logger that already "
                "exists — including bin/migrate.py's, which is where the "
                "single-practice migration explains why it is blocking a deploy."
            )
            value = kwargs["disable_existing_loggers"]
            assert isinstance(value, ast.Constant), (
                "disable_existing_loggers is computed rather than literal, so "
                "whether the migrate job can report is decided at runtime"
            )
            assert value.value is False, (
                "disable_existing_loggers must be False; anything else silences "
                "the migrate job's own reporting"
            )

    def test_a_logger_made_before_fileconfig_still_emits(self) -> None:
        """The property itself, against the real alembic.ini.

        Guards the case where somebody keeps the keyword but points env.py at a
        config whose own ``[loggers]`` section disables things.

        Captured on the logger rather than through ``caplog``: ``fileConfig``
        replaces the root handlers, which removes pytest's, so caplog would
        report silence here even when the logger is perfectly alive. That is
        the test lying about the product, and it is exactly the failure mode
        this class exists to catch, so it is worth not reproducing.
        """
        import logging  # noqa: PLC0415
        from logging.config import fileConfig  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415

        before = logging.getLogger("pablo.test.made_before_alembic")
        ini = _Path(__file__).resolve().parents[1] / "alembic.ini"

        fileConfig(str(ini), disable_existing_loggers=False)

        assert not before.disabled, (
            "a logger created before alembic configured logging was disabled by it"
        )

        seen: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                seen.append(record.getMessage())

        handler = _Capture()
        before.addHandler(handler)
        before.setLevel(logging.ERROR)
        try:
            before.error("the refusal a deploy depends on")
        finally:
            before.removeHandler(handler)

        assert seen == ["the refusal a deploy depends on"]
