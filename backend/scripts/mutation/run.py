# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Run every mutant against a set of tests and record which tests killed it.

From ``backend/``::

    python -m scripts.mutation.run --preset calendar --out /tmp/mutation/calendar
    python -m scripts.mutation.score /tmp/mutation/calendar

Each worker gets its own copy of ``backend/``, writes one mutant into it, runs
the tests there and puts the module back, so workers never share a file and
the checkout itself is never touched. The first run writes ``mutants.json``;
``results.jsonl`` gains one line per mutant as it finishes, so a stopped run
resumes where it left off. ``--baseline`` runs the tests once on the modules
as the harness rewrites them (parsed and unparsed, unmutated): it must pass,
or every mutant would look killed.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from scripts.mutation.generate import Mutant, NodePath, generate
from scripts.mutation.presets import PRESETS

if TYPE_CHECKING:
    from collections.abc import Callable

BACKEND = Path(__file__).resolve().parents[2]
_COPY_IGNORE = shutil.ignore_patterns(
    "__pycache__", "tests_integration", "evals", ".pytest_cache", "htmlcov"
)
_PER_TEST_TIMEOUT = 60
_RUN_TIMEOUT = 240
_PROGRESS_EVERY = 15


# --- rebuilding a mutant ---------------------------------------------------


def _navigate(tree: ast.AST, path: NodePath) -> Any:
    node: Any = tree
    for field, index in path:
        value = getattr(node, field)
        node = value if index is None else value[index]
    return node


def _replace(tree: ast.AST, path: NodePath, new: ast.AST) -> None:
    parent = _navigate(tree, path[:-1])
    field, index = path[-1]
    if index is None:
        setattr(parent, field, new)
    else:
        getattr(parent, field)[index] = new


def _early_return(tree: ast.AST, node: Any, m: Mutant) -> None:
    del tree
    node.body.insert(m["insert_index"], ast.Return(value=ast.Constant(value=None)))


def _delete_stmt(tree: ast.AST, _node: Any, m: Mutant) -> None:
    _replace(tree, m["path"], ast.Pass())


def _compare(_tree: ast.AST, node: Any, m: Mutant) -> None:
    node.ops[m["op_index"]] = getattr(ast, m["new_op"])()


def _swap_operator(_tree: ast.AST, node: Any, m: Mutant) -> None:
    node.op = getattr(ast, m["new_op"])()


def _drop_not(tree: ast.AST, node: Any, m: Mutant) -> None:
    _replace(tree, m["path"], node.operand)


def _constant(_tree: ast.AST, node: Any, m: Mutant) -> None:
    node.value = m["new_value"]


def _return_none(_tree: ast.AST, node: Any, _m: Mutant) -> None:
    node.value = ast.Constant(value=None)


def _cond_const(_tree: ast.AST, node: Any, m: Mutant) -> None:
    node.test = ast.Constant(value=m["new_value"])


def _comp_if_true(_tree: ast.AST, node: Any, m: Mutant) -> None:
    node.ifs[m["if_index"]] = ast.Constant(value=True)


def _name_swap(_tree: ast.AST, node: Any, m: Mutant) -> None:
    node.id = m["new_value"]


def _attr_swap(_tree: ast.AST, node: Any, m: Mutant) -> None:
    node.attr = m["new_value"]


def _drop_kwarg(_tree: ast.AST, node: Any, m: Mutant) -> None:
    del node.keywords[m["kw_index"]]


_APPLY: dict[str, Callable[[ast.AST, Any, Mutant], None]] = {
    "early_return": _early_return,
    "delete_stmt": _delete_stmt,
    "compare": _compare,
    "boolop": _swap_operator,
    "binop": _swap_operator,
    "augassign": _swap_operator,
    "drop_not": _drop_not,
    "const_bool": _constant,
    "const_int": _constant,
    "const_str": _constant,
    "return_none": _return_none,
    "cond_const": _cond_const,
    "comp_if_true": _comp_if_true,
    "name_swap": _name_swap,
    "attr_swap": _attr_swap,
    "drop_kwarg": _drop_kwarg,
}


def mutated_source(original: str, mutant: Mutant) -> str:
    """The module's source with this one mutant applied."""
    tree = ast.parse(original)
    _APPLY[mutant["op"]](tree, _navigate(tree, mutant["path"]), mutant)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree)


def unmutated_source(original: str) -> str:
    """The module as the harness writes it, with nothing changed: the baseline."""
    return ast.unparse(ast.parse(original))


# --- running tests ---------------------------------------------------------


def _run_tests(worker_backend: Path, tests: tuple[str, ...]) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-o",
        "addopts=",
        "-p",
        "no:cacheprovider",
        "-q",
        "--no-header",
        "-rfE",
        f"--timeout={_PER_TEST_TIMEOUT}",
        *tests,
    ]
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    started = time.monotonic()
    try:
        proc = subprocess.run(
            command,
            cwd=worker_backend,
            env=env,
            capture_output=True,
            text=True,
            timeout=_RUN_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        elapsed = round(time.monotonic() - started, 2)
        return {"status": "timeout", "failed": [], "errors": [], "elapsed": elapsed}
    failed = [
        line[7:].split(" - ")[0].strip()
        for line in proc.stdout.splitlines()
        if line.startswith("FAILED ")
    ]
    errors = [
        line[6:].split(" - ")[0].strip()
        for line in proc.stdout.splitlines()
        if line.startswith("ERROR ")
    ]
    if proc.returncode == 0:
        status = "survived"
    elif failed or errors:
        status = "killed"
    else:
        # Collection broke, or pytest itself failed: neither caught nor survived.
        status = f"rc{proc.returncode}"
    return {
        "status": status,
        "failed": failed,
        "errors": errors,
        "rc": proc.returncode,
        "elapsed": round(time.monotonic() - started, 2),
        "tail": proc.stdout[-600:] if status.startswith("rc") else "",
    }


class _Pool:
    """Workers, each with its own copy of backend/, sharing one queue of mutants."""

    def __init__(self, src: Path, out: Path, tests: tuple[str, ...], originals: dict[str, str]):
        self.src = src
        self.out = out
        self.tests = tests
        self.originals = originals
        self.lock = threading.Lock()
        self.queue: queue.Queue[Mutant] = queue.Queue()

    def worker_backend(self, index: int) -> Path:
        return self.out / "workers" / f"w{index}" / "backend"

    def prepare(self, count: int) -> None:
        for index in range(count):
            backend = self.worker_backend(index)
            if backend.exists():
                shutil.rmtree(backend)
            shutil.copytree(self.src, backend, ignore=_COPY_IGNORE)
            shutil.copyfile(self.src.parent / "pyproject.toml", backend.parent / "pyproject.toml")

    def baseline(self) -> dict[str, Any]:
        backend = self.worker_backend(0)
        for rel, original in self.originals.items():
            (backend / rel).write_text(unmutated_source(original))
        try:
            return _run_tests(backend, self.tests)
        finally:
            self._restore(backend)

    def _restore(self, backend: Path) -> None:
        for rel, original in self.originals.items():
            (backend / rel).write_text(original)

    def _one(self, backend: Path, mutant: Mutant) -> dict[str, Any]:
        original = self.originals[mutant["file"]]
        try:
            source = mutated_source(original, mutant)
        except Exception as exc:
            # A mutant that cannot be rebuilt is recorded, not fatal to the run.
            return {"id": mutant["id"], "status": "gen_error", "error": repr(exc)}
        if source == unmutated_source(original):
            return {"id": mutant["id"], "status": "noop"}
        target = backend / mutant["file"]
        target.write_text(source)
        try:
            return {"id": mutant["id"], **_run_tests(backend, self.tests)}
        finally:
            target.write_text(original)

    def _work(self, index: int, results: Path) -> None:
        backend = self.worker_backend(index)
        while True:
            try:
                mutant = self.queue.get_nowait()
            except queue.Empty:
                self._restore(backend)
                return
            record = self._one(backend, mutant)
            with self.lock, results.open("a") as fh:
                fh.write(json.dumps(record) + "\n")

    def run(self, todo: list[Mutant], workers: int, results: Path) -> None:
        for mutant in todo:
            self.queue.put(mutant)
        threads = [
            threading.Thread(target=self._work, args=(index, results)) for index in range(workers)
        ]
        started = time.monotonic()
        for thread in threads:
            thread.start()
        while any(thread.is_alive() for thread in threads):
            time.sleep(_PROGRESS_EVERY)
            remaining = self.queue.qsize()
            print(f"~{remaining} left after {int(time.monotonic() - started)}s", file=sys.stderr)
        for thread in threads:
            thread.join()


# --- entry point -----------------------------------------------------------


def _done_ids(results: Path) -> set[int]:
    if not results.exists():
        return set()
    with results.open() as fh:
        return {json.loads(line)["id"] for line in fh if line.strip()}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--preset", choices=sorted(PRESETS), help="modules and tests to use")
    parser.add_argument("--modules", nargs="*", help="modules to mutate, relative to backend/")
    parser.add_argument("--tests", nargs="*", help="test files to run, relative to backend/")
    parser.add_argument("--out", type=Path, required=True, help="directory for this run")
    parser.add_argument("--src", type=Path, default=BACKEND, help="backend/ to mutate a copy of")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--limit", type=int, default=0, help="run at most this many mutants")
    parser.add_argument("--baseline", action="store_true", help="only check the unmutated run")
    args = parser.parse_args(argv)
    preset = PRESETS.get(args.preset) if args.preset else None
    args.modules = tuple(args.modules or (preset.modules if preset else ()))
    args.tests = tuple(args.tests or (preset.tests if preset else ()))
    if not args.modules or not args.tests:
        parser.error("name a --preset, or give both --modules and --tests")
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    src: Path = args.src.resolve()
    out: Path = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)

    mutants_path = out / "mutants.json"
    if mutants_path.exists():
        mutants = json.loads(mutants_path.read_text())
    else:
        mutants = generate(src, args.modules)
        mutants_path.write_text(json.dumps(mutants, indent=0))
    originals = {rel: (src / rel).read_text() for rel in {m["file"] for m in mutants}}

    pool = _Pool(src, out, args.tests, originals)
    workers = 1 if args.baseline else args.workers
    pool.prepare(workers)
    baseline = pool.baseline()
    print(f"baseline: {baseline['status']} ({baseline['elapsed']}s)", file=sys.stderr)
    if baseline["status"] != "survived":
        print("The unmutated modules fail their own tests; fix that first.", file=sys.stderr)
        print(baseline.get("tail", ""), file=sys.stderr)
        return 1
    if args.baseline:
        return 0

    results = out / "results.jsonl"
    done = _done_ids(results)
    todo = [m for m in mutants if m["id"] not in done]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(mutants)} mutants, {len(todo)} to run on {workers} workers", file=sys.stderr)
    pool.run(todo, workers, results)
    print(f"done; score with: python -m scripts.mutation.score {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
