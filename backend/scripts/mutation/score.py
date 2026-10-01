# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Score a run, carrying forward what earlier runs decided about survivors.

From ``backend/``::

    python -m scripts.mutation.score /tmp/mutation/calendar [/tmp/mutation/older ...]
    python -m scripts.mutation.score /tmp/mutation/calendar --survivors

A killed mutant is caught. A surviving one is looked up in ``classified.json``
by what it is (file, function, operator, description, source text, and which
occurrence of that combination it is), not by id or line number, so a
judgement made once holds while the module around it changes:

* ``A`` a real gap: the change models a bug, and no test notices.
* ``D`` untested: the whole function or path is never run by these tests.
* ``B`` equivalent: the change cannot alter behaviour.
* ``C`` harmless: log or message text, counts only logged, performance only.
* ``E`` dead code: nothing calls it.
* ``F`` hidden by an in-memory repository sharing objects with its caller.
* ``?`` not judged yet: new code, or changed since it was judged.

The raw score is caught over everything. The adjusted score leaves out
``B``, ``C``, ``E`` and ``F``, which no test could or should catch.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from scripts.mutation.generate import Mutant

CLASSIFIED = Path(__file__).with_name("classified.json")
EXCLUDED = frozenset({"B", "C", "E", "F"})
SURVIVING = ("A", "D", "?")

Key = tuple[str, str, str, str, str, int]


def content_keys(mutants: list[Mutant]) -> dict[int, Key]:
    """Each mutant's id mapped to the key a classification is stored under."""
    seen: collections.Counter[tuple[str, ...]] = collections.Counter()
    keys: dict[int, Key] = {}
    for m in mutants:
        base = (m["file"], m["func"], m["op"], m["desc"], m["snippet"])
        keys[m["id"]] = (*base, seen[base])
        seen[base] += 1
    return keys


def load_classified(path: Path = CLASSIFIED) -> dict[Key, str]:
    rows = json.loads(path.read_text())["survivors"]
    return {
        (r["file"], r["func"], r["op"], r["desc"], r["snippet"], r["occurrence"]): r["class"]
        for r in rows
    }


def load_run(run: Path) -> tuple[list[Mutant], dict[int, dict[str, Any]]]:
    mutants = json.loads((run / "mutants.json").read_text())
    results: dict[int, dict[str, Any]] = {}
    with (run / "results.jsonl").open() as fh:
        for line in fh:
            if line.strip():
                record = json.loads(line)
                results[record["id"]] = record
    return mutants, results


def classify_run(
    mutants: list[Mutant], results: dict[int, dict[str, Any]], classified: dict[Key, str]
) -> list[tuple[Mutant, str]]:
    """Each mutant that ran, with ``K`` for killed or its survivor class."""
    keys = content_keys(mutants)
    out: list[tuple[Mutant, str]] = []
    for m in mutants:
        record = results.get(m["id"])
        if record is None or record["status"] in {"noop", "gen_error"}:
            continue
        cls = "K" if record["status"] == "killed" else classified.get(keys[m["id"]], "?")
        out.append((m, cls))
    return out


def score_line(counts: collections.Counter[str]) -> str:
    total = sum(counts.values())
    caught = counts["K"]
    denominator = total - sum(counts[c] for c in EXCLUDED)
    raw = f"{caught}/{total} = {caught / total:.0%}" if total else "0/0"
    adjusted = f"{caught}/{denominator} = {caught / denominator:.0%}" if denominator else "0/0"
    classes = " ".join(f"{c}={counts[c]}" for c in ("A", "D", "B", "C", "E", "F", "?"))
    return f"raw {raw} | adjusted {adjusted} | {classes}"


def report(run: Path, classified: dict[Key, str], *, survivors: bool) -> None:
    mutants, results = load_run(run)
    judged = classify_run(mutants, results, classified)
    by_module: dict[str, collections.Counter[str]] = collections.defaultdict(collections.Counter)
    for m, cls in judged:
        by_module[m["file"]][cls] += 1
    print(f"=== {run.name}: {len(judged)} of {len(mutants)} mutants scored ===")
    for module, counts in sorted(by_module.items()):
        print(f"{module}: {score_line(counts)}")
    if survivors:
        _print_survivors(judged)
    print()


def _print_survivors(judged: list[tuple[Mutant, str]]) -> None:
    by_function: dict[tuple[str, str], collections.Counter[str]] = collections.defaultdict(
        collections.Counter
    )
    unjudged: list[Mutant] = []
    for m, cls in judged:
        if cls in SURVIVING:
            by_function[(cls, m["file"])][m["func"]] += 1
        if cls == "?":
            unjudged.append(m)
    for cls in SURVIVING:
        for (c, module), functions in sorted(by_function.items()):
            if c != cls:
                continue
            print(f"  {cls} in {module}:")
            for function, count in functions.most_common():
                print(f"    {count:4d}  {function}")
    for m in unjudged:
        print(f"  ? [{m['id']}] {m['file']}:{m['line']} {m['func']} | {m['desc']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", nargs="+", type=Path, help="run directories, oldest first")
    parser.add_argument("--survivors", action="store_true", help="list what survived, by function")
    parser.add_argument("--classified", type=Path, default=CLASSIFIED)
    args = parser.parse_args(argv)
    classified = load_classified(args.classified)
    for run in args.runs:
        report(run, classified, survivors=args.survivors)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
