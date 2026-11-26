#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Regression guard for ``scripts/check_code_ratchet.py``.

Each case plants one change in a fresh scratch tree whose baseline was written
before the plant, and asserts pass or fail plus the files the message names.
Last, the diff numbers are read from a scratch git repo.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

_CHECK = Path(__file__).resolve().parent / "check_code_ratchet.py"
_spec = importlib.util.spec_from_file_location("check_code_ratchet", _CHECK)
assert _spec and _spec.loader
mod = importlib.util.module_from_spec(_spec)
sys.modules["check_code_ratchet"] = mod
_spec.loader.exec_module(mod)

LONG = """
def {name}(rows{ann}):
    {doc}total = {start}
    for row in rows:
        if row is None:
            continue
        total += row
    return total
"""
SHORT = "\ndef {name}(dt):\n    return dt if dt.tzinfo else dt.replace(tzinfo=None)\n"
DELEGATE = "def main(argv):\n    return run(argv)\n"
BIG = "x = 1\n" * 900
# The heredoc's lone brace must not end the function.
LONG_SH = """#!/usr/bin/env bash
{name}() {{
  local total=0 x
  for x in "$@"; do
    total=$((total + x))
  done
  cat <<EOF
}}
EOF
  echo "$total"
}}
"""
LONG_SH_NOISY = """
# helpers
{name}() {{   # sum the args

    local total=0 x
    for x in "$@"; do   # each one
      total=$((total + x))
    done

    cat <<EOF
}}
EOF
    echo "$total"
}}
"""
SHORT_SH = 'die() { echo "box: $*" >&2; exit 1; }\n'
TRIVIAL_SH = 'log() { echo "$*" >&2; }\n'
BROKEN_SH = [
    'unclosed() {\n  { echo "a"; exit 1\n}\n',
    'quoted() {\n  echo "open; exit 1\n}\n',
    "skewed() {\n  echo b; exit 1\n  }\n",
]
HEREDOC_SH = "show() {{\n  local x; x=1\n  cat <<EOF\n}}\nEOF\n  echo {tail}\n}}\n"


def _long(name: str, ann: str = "", doc: str = "", start: int = 0) -> str:
    return LONG.format(name=name, ann=ann, doc=doc, start=start)


def _write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _run(root: Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = mod.main(["--root", str(root), *args])
    return code, out.getvalue()


def _scaffold(root: Path) -> None:
    """A tree with one grandfathered large file and one baselined duplicate pair."""
    _write(root, "app/big.py", BIG)
    _write(root, "app/a.py", _long("tally"))
    _write(root, "app/b.py", _long("tally"))
    _write(root, "app/c.py", DELEGATE)
    _write(root, "app/d.py", DELEGATE)
    _write(root, "app/e.py", "def helper(x):\n    return x + 1\n")
    _write(root, "sh/a.sh", LONG_SH.format(name="tally"))
    _write(root, "sh/b", LONG_SH.format(name="tally"))
    _write(root, "tests/test_a.py", _long("_seed", start=2))
    _write(root, "tests/test_b.py", _long("_seed", start=2))
    assert _run(root, "--write-baseline")[0] == 0


def _exempt(root: Path, path: str, reason: str) -> None:
    baseline = root / mod.BASELINE
    data = json.loads(baseline.read_text(encoding="utf-8"))
    data.setdefault("exempt", {})[path] = reason
    baseline.write_text(json.dumps(data), encoding="utf-8")


Plant = Callable[[Path], object]
Case = tuple[str, Plant, bool, list[str]]
CASES: list[Case] = [
    ("scaffold unchanged", lambda r: None, True, []),
    ("new file over 800", lambda r: _write(r, "app/new.sh", "echo\n" * 801), False, ["app/new.sh"]),
    ("baselined file grows", lambda r: _write(r, "app/big.py", BIG + "y\n"), False, ["app/big.py"]),
    ("baselined file shrinks", lambda r: _write(r, "app/big.py", "x = 1\n" * 850), True, []),
    (
        "exempt table grows",
        lambda r: (_exempt(r, "app/big.py", "a code list"), _write(r, "app/big.py", BIG + "y\n")),
        True,
        [],
    ),
    (
        "exemption with no reason",
        lambda r: _exempt(r, "app/big.py", " "),
        False,
        ["app/big.py: exempt from the size rule with no reason"],
    ),
    (
        "rewriting the baseline keeps an exemption",
        lambda r: (_exempt(r, "app/big.py", "a code list"), _run(r, "--write-baseline")),
        True,
        [],
    ),
    (
        "renamed 6+ line copy",
        lambda r: _write(r, "app/f.py", _long("sum_rows")),
        False,
        ["app/a.py:tally", "app/f.py:sum_rows"],
    ),
    (
        "same-name short copy",
        lambda r: (
            _write(r, "app/g.py", SHORT.format(name="_aware")),
            _write(r, "app/h.py", SHORT.format(name="_aware")),
        ),
        False,
        ["app/g.py:_aware", "app/h.py:_aware"],
    ),
    (
        "annotation and docstring differ only",
        lambda r: (
            _write(r, "app/i.py", _long("count_a", start=1)),
            _write(r, "app/j.py", _long("count_b", ": list[int]", '"""Sum."""\n    ', start=1)),
        ),
        False,
        ["app/i.py:count_a", "app/j.py:count_b"],
    ),
    (
        "third copy of a baselined group",
        lambda r: _write(r, "app/k.py", _long("tally")),
        False,
        ["app/a.py:tally", "app/k.py:tally"],
    ),
    ("baselined duplicate removed", lambda r: (r / "app/b.py").unlink(), True, []),
    (
        "different functions share a name",
        lambda r: _write(r, "app/l.py", "def helper(x):\n    return x * 2\n"),
        True,
        [],
    ),
    ("delegate copies", lambda r: _write(r, "app/m.py", DELEGATE), True, []),
    ("stub copies", lambda r: _write(r, "app/n.py", "def helper(x):\n    ...\n"), True, []),
    (
        "test size has no ceiling; one test copy of a source function is no group",
        lambda r: (
            _write(r, "tests/test_big.py", BIG),
            _write(r, "tests/test_x.py", _long("tally")),
            _write(r, "scripts/x_selftest.py", BIG),
            _write(r, "web/a.spec.ts", "x;\n" * 900),
            _write(r, "db/alembic/versions/0001.py", BIG),
        ),
        True,
        [],
    ),
    (
        "helper copied into a second test file",
        lambda r: (
            _write(r, "tests/test_c.py", _long("_make", start=3)),
            _write(r, "tests_integration/test_d.py", _long("_build", start=3)),
        ),
        False,
        ["tests/test_c.py:_make", "tests_integration/test_d.py:_build"],
    ),
    (
        "third copy of a baselined test group",
        lambda r: _write(r, "tests/test_e.py", _long("_seed", start=2)),
        False,
        ["tests/test_a.py:_seed", "tests/test_e.py:_seed"],
    ),
    ("baselined test duplicate removed", lambda r: (r / "tests/test_b.py").unlink(), True, []),
    (
        "same-name short fixture in two conftests",
        lambda r: (
            _write(r, "tests/a/conftest.py", SHORT.format(name="_aware")),
            _write(r, "tests/b/conftest.py", SHORT.format(name="_aware")),
        ),
        False,
        ["tests/a/conftest.py:_aware", "tests/b/conftest.py:_aware"],
    ),
    (
        "renamed shell copy",
        lambda r: _write(r, "sh/c.sh", LONG_SH.format(name="sum_args")),
        False,
        ["sh/a.sh:tally", "sh/c.sh:sum_args"],
    ),
    (
        "same-name short shell copy",
        lambda r: (_write(r, "sh/d.sh", SHORT_SH), _write(r, "sh/e.sh", SHORT_SH)),
        False,
        ["sh/d.sh:die", "sh/e.sh:die"],
    ),
    (
        "shell comments and whitespace differ only",
        lambda r: (
            _write(r, "sh/f.sh", LONG_SH.format(name="count")),
            _write(r, "sh/g.sh", LONG_SH_NOISY.format(name="count")),
        ),
        False,
        ["sh/f.sh:count", "sh/g.sh:count"],
    ),
    (
        "trivial shell one-liners",
        lambda r: (_write(r, "sh/h.sh", TRIVIAL_SH), _write(r, "sh/i.sh", TRIVIAL_SH)),
        True,
        [],
    ),
    (
        "third copy of a baselined shell group",
        lambda r: _write(r, "sh/j.sh", LONG_SH.format(name="tally")),
        False,
        ["sh/b:tally", "sh/j.sh:tally"],
    ),
    (
        "a brace in a heredoc does not end the body",
        lambda r: (
            _write(r, "sh/m.sh", HEREDOC_SH.format(tail="a")),
            _write(r, "sh/n.sh", HEREDOC_SH.format(tail="b")),
        ),
        True,
        [],
    ),
    (
        "unbalanced shell bodies skipped",
        lambda r: [_write(r, f"sh/{n}{i}.sh", s) for i, s in enumerate(BROKEN_SH) for n in "kl"],
        True,
        [],
    ),
]


def _plant_case(scratch: Path, base: Path, case: Case) -> list[str]:
    name, plant, ok, named = case
    root = scratch / name.replace(" ", "_")
    shutil.copytree(base, root)
    plant(root)
    code, out = _run(root)
    failures = []
    if (code == 0) != ok:
        failures.append(f"{name}: expected {'pass' if ok else 'fail'}, got exit {code}\n{out}")
    failures += [f"{name}: message does not name {n}\n{out}" for n in named if n not in out]
    return failures


def _language_case(root: Path) -> list[str]:
    """A Python and a shell function with an identical digest still form no group."""
    _write(root, "app/x.py", "pass\n")
    _write(root, "sh/x.sh", LONG_SH.format(name="tally"))
    real = mod.functions
    mod.functions = lambda text: mod.shell_functions(LONG_SH.format(name="tally"))
    try:
        dups = mod.scan(root)[1]
    finally:
        mod.functions = real
    return [f"language namespace: shell and Python grouped: {dups}"] if dups else []


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t", *args],
        check=True,
        capture_output=True,
    )


def _diff_case(root: Path) -> list[str]:
    """Three product lines (a docstring and a comment among them), three test lines."""
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _write(root, "app/a.py", "x = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "base")
    _git(root, "checkout", "-qb", "pr")
    _write(root, "app/a.py", '"""Doc."""\nx = 1\n# why\ny = 2\n')
    _write(root, "tests/test_a.py", "def test():\n    assert 1\n    assert 2\n")
    _write(root, "README.md", "prose\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "pr")
    code, out = _run(root, "--report", "--base", "main")
    want = [
        "added_source_lines=3",
        "added_test_lines=3",
        "comment_share=0.333",
        "test_to_product=1.000",
    ]
    return [f"diff numbers: missing {w}\n{out}" for w in want if w not in out.splitlines()] + (
        [f"diff numbers: --report exited {code}"] if code else []
    )


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp)
        base = scratch / "base"
        _scaffold(base)
        for case in CASES:
            failures += _plant_case(scratch, base, case)
        failures += _language_case(scratch / "language")
        failures += _diff_case(scratch / "git")
    for failure in failures:
        print(f"FAIL {failure}")
    if failures:
        return 1
    print(f"check_code_ratchet selftest OK: {len(CASES) + 2} cases")
    return 0


if __name__ == "__main__":
    sys.exit(main())
