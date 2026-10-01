# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The mutation harness: every mutant it makes is real, and a judgement outlives an edit.

The harness only earns a score if each mutant is a module that still parses
and differs from the original, and if a survivor judged in one run is found
again in the next after the code around it moved.
"""

from __future__ import annotations

import ast
import textwrap

from scripts.mutation.generate import generate_module
from scripts.mutation.run import mutated_source, unmutated_source
from scripts.mutation.score import content_keys

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
