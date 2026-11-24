# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The mutation harness: every mutant it makes is real, and a judgement outlives an edit.

The harness only earns a score if each mutant is a module that still parses
and differs from the original, and if a survivor judged in one run is found
again in the next after the code around it moved.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.mutation import run
from scripts.mutation.generate import generate_module
from scripts.mutation.presets import PRESETS
from scripts.mutation.run import mutated_source, unmutated_source
from scripts.mutation.score import content_keys

if TYPE_CHECKING:
    import pytest

SOURCE = textwrap.dedent(
    '''
    """A module the harness can chew on."""

    from __future__ import annotations

    import logging
    from typing import TYPE_CHECKING

    if TYPE_CHECKING:
        from collections.abc import Iterable

    logger = logging.getLogger(__name__)
    __all__ = ["pick"]


    class Picker:
        limit: int = 3

        def pick(self, names: Iterable[str], *, strict: bool = False) -> list[str]:
            """Names that start with a capital, at most ``limit`` of them."""
            chosen = [n.strip() for n in names if n and n[0].isupper()]
            chosen.sort(reverse=False)
            if strict and not chosen:
                msg = "nothing to pick"
                raise ValueError(msg)
            total = 0
            while total < self.limit:
                total += 1
            logger.info("picked %d", len(chosen))
            return chosen[: min(total, len(chosen))] if chosen else []
    '''
)


def _mutants() -> list[dict]:
    mutants = generate_module("app/picker.py", SOURCE)
    for index, mutant in enumerate(mutants):
        mutant["id"] = index
    return mutants


def test_every_mutant_parses_and_changes_the_module() -> None:
    baseline = unmutated_source(SOURCE)
    for mutant in _mutants():
        mutated = mutated_source(SOURCE, mutant)
        ast.parse(mutated)
        assert mutated != baseline, mutant["desc"]


def test_it_mutates_each_kind_of_node_it_claims_to() -> None:
    ops = {m["op"] for m in _mutants()}
    assert ops >= {
        "early_return",
        "delete_stmt",
        "compare",
        "boolop",
        "drop_not",
        "augassign",
        "const_bool",
        "const_int",
        "const_str",
        "return_none",
        "cond_const",
        "comp_if_true",
        "name_swap",
        "attr_swap",
        "drop_kwarg",
    }


def test_it_leaves_imports_annotations_and_docstrings_alone() -> None:
    snippets = [m["snippet"] for m in _mutants()]
    assert not any("import" in s for s in snippets)
    assert not any("A module the harness" in s for s in snippets)
    assert not any(s == "TYPE_CHECKING" for s in snippets)
    # ``bool`` in ``strict: bool`` is an annotation, never a mutant's target.
    assert not any(m["op"] == "name_swap" and m["snippet"] == "bool" for m in _mutants())


def test_log_and_message_text_is_tagged() -> None:
    tags = {m["snippet"]: m.get("tag") for m in _mutants() if m["op"] == "const_str"}
    assert tags["'picked %d'"] == "log"
    assert tags["'nothing to pick'"] == "msg"


def test_a_judgement_is_found_again_after_the_module_moves() -> None:
    before = content_keys(_mutants())
    shifted = "\n\ndef helper() -> None:\n    pass\n" + SOURCE.replace(
        '"""A module the harness can chew on."""', ""
    )
    after_mutants = generate_module("app/picker.py", shifted)
    for index, mutant in enumerate(after_mutants):
        mutant["id"] = index
    after = content_keys(after_mutants)
    # Every mutant of the original code keeps its key; only the new helper adds keys.
    assert set(before.values()) <= set(after.values())


class _FakePytest:
    """Stands in for a pytest invocation: records what was asked, answers as told."""

    def __init__(self, statuses: dict[str, str]) -> None:
        self.statuses = statuses
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []

    def __call__(
        self, _backend: Path, tests: tuple[str, ...], _timeout: int, *extra: str
    ) -> dict[str, Any]:
        self.calls.append((tests, extra))
        return {"status": self.statuses[tests[0]], "failed": [], "errors": [], "elapsed": 1.5}


def _two_phase(monkeypatch: pytest.MonkeyPatch, unit: str, integration: str) -> _FakePytest:
    fake = _FakePytest({"tests/unit.py": unit, "tests_integration/db.py": integration})
    monkeypatch.setattr(run, "_pytest", fake)
    return fake


def test_a_mutant_the_unit_tests_catch_never_reaches_a_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _two_phase(monkeypatch, unit="killed", integration="survived")

    result = run._run_tests(Path(), ("tests/unit.py",), ("tests_integration/db.py",))

    assert result["status"] == "killed"
    assert fake.calls == [(("tests/unit.py",), ())]


def test_a_mutant_the_unit_tests_miss_is_given_to_the_integration_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """As a second invocation, stopping at the first failure, with both runs' time."""
    fake = _two_phase(monkeypatch, unit="survived", integration="killed")

    result = run._run_tests(Path(), ("tests/unit.py",), ("tests_integration/db.py",))

    assert result["status"] == "killed"
    assert fake.calls == [(("tests/unit.py",), ()), (("tests_integration/db.py",), ("-x",))]
    assert result["elapsed"] == 3.0


def test_it_survives_only_if_both_suites_let_it_through(monkeypatch: pytest.MonkeyPatch) -> None:
    _two_phase(monkeypatch, unit="survived", integration="survived")

    result = run._run_tests(Path(), ("tests/unit.py",), ("tests_integration/db.py",))

    assert result["status"] == "survived"


def test_a_preset_may_have_integration_tests_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _two_phase(monkeypatch, unit="survived", integration="killed")

    result = run._run_tests(Path(), (), ("tests_integration/db.py",))

    assert result["status"] == "killed"
    assert fake.calls == [(("tests_integration/db.py",), ("-x",))]


def test_every_preset_names_files_that_exist() -> None:
    """A preset whose test file was renamed would score every mutant as caught or none."""
    for name, preset in PRESETS.items():
        for rel in (*preset.modules, *preset.tests, *preset.integration_tests):
            assert (run.BACKEND / rel).is_file(), f"{name}: {rel}"
        assert all(rel.startswith("tests/") for rel in preset.tests), name
        assert all(rel.startswith("tests_integration/") for rel in preset.integration_tests), name
