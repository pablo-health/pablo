#!/usr/bin/env python3
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
"""Code-quality ratchet: file size and copied Python and shell functions may not get worse.

    python3 scripts/check_code_ratchet.py [--base REF] [--report | --write-baseline]

BLOCKING. (1) A non-test source file may not cross CEILING lines; one already
over may not grow past its size in the baseline. (2) A Python function whose
normalised form is MIN_LINES or more may not appear in a second file.
(3) A shorter one may not appear in a second file under the same name, unless
it only delegates (``return run(argv)``). Stubs (pass, ...) are ignored.
Normalised means the docstring and annotations are dropped and the syntax tree
is hashed, so a renamed copy still matches. Shell functions (.sh, or a sh/bash
shebang) follow the same rules on their body lines, comments and blank lines
dropped; a one-command body counts as a delegate, and a body whose closing
brace cannot be found is skipped. A group in the baseline passes while its
file count does not rise. (4) Rules 2 and 3 also hold among test files, counted
apart from source: a helper or fixture copied into a second test module fails.
Test files have no size ceiling. A data table (a code list, say) is let off the
size rule by an entry under "exempt" in the baseline, path to reason; an entry
with no reason fails.

REPORTED, never blocking (they are heuristics): comment and docstring share of
the PR's added lines, tests-to-product ratio, and added non-test source lines,
with a warning past LARGE_DIFF. Needs --base; skipped without it.

The baseline (scripts/code_ratchet_baseline.json) only ever needs to shrink,
and a stale entry is never an error. Raising it is a CODEOWNERS review.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = "scripts/code_ratchet_baseline.json"
CEILING = 800
MIN_LINES = 6
LARGE_DIFF = 500
MIN_PYTHON = (3, 13)

SOURCE_SUFFIXES = {".py", ".sh", ".ts", ".tsx", ".mjs"}
TEST_DIRS = {"tests", "tests_integration", "tests_support", "__tests__", "e2e"}
SKIP_DIRS = {"node_modules", ".github", "data", "fixtures", "generated", "dist", "build"}
SH_SHEBANG = r"^#!.*\b(?:ba|z)?sh\b"
SHEBANG = re.compile(SH_SHEBANG.encode() + rb"|^#!.*\bpython")
SH_DEF = re.compile(
    r"(\s*)(?:function\s+([\w:.-]+)\s*(?:\(\s*\))?|([\w:.-]+)\s*\(\s*\))"
    r"\s*\{(?:\s(.*?);?\s*\})?\s*$"
)
SH_HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_]\w*)\1")
SH_COMPOUND = re.compile(r"[;|`]|&&|\$\(|^(?:if|for|while|until|case)\b")
HUNK = re.compile(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@")


def kind(path: str, root: Path) -> str | None:
    """'test', 'source', or None for a path the ratchet does not count."""
    parts = path.split("/")
    name = parts[-1]
    if SKIP_DIRS & set(parts[:-1]) or ".gen." in name or name.endswith(".d.ts"):
        return None
    if "versions" in parts and any(p.startswith("alembic") for p in parts):
        return None
    suffix = Path(name).suffix
    if suffix not in SOURCE_SUFFIXES:
        if suffix or not _has_shebang(root / path):
            return None
    is_test = (
        TEST_DIRS & set(parts[:-1])
        or name.startswith("test_")
        or name == "conftest.py"
        or "selftest" in name
        or re.search(r"[._]test\.|\.spec\.", name)
    )
    return "test" if is_test else "source"


def _has_shebang(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return bool(SHEBANG.match(fh.readline(200)))
    except OSError:
        return False


def list_files(root: Path) -> list[str]:
    if (root / ".git").exists():
        out = _git(root, "ls-files", "-z")
        return sorted(p for p in out.split("\0") if p and (root / p).is_file())
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "node_modules"]
        found += [(Path(dirpath) / f).relative_to(root).as_posix() for f in filenames]
    return sorted(found)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


class _Strip(ast.NodeTransformer):
    """Drop annotations so a copy that differs only in types still matches."""

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.annotation = None
        return node

    def visit_AnnAssign(self, node: ast.AnnAssign) -> ast.AST:
        node.annotation = ast.Constant(None)
        return self.generic_visit(node)


def _body(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[ast.stmt]:
    """The body without its docstring; empty for a stub (pass, ..., NotImplementedError)."""
    body = fn.body
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
        if isinstance(first.value.value, str):
            body = body[1:]
    if all(_stub(stmt) for stmt in body):
        return []
    return body


def _stub(stmt: ast.stmt) -> bool:
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant):
        return True
    if isinstance(stmt, ast.Raise):
        return "NotImplementedError" in ast.dump(stmt)
    return isinstance(stmt, ast.Pass)


def _delegate(body: list[ast.stmt]) -> bool:
    """One statement that only returns a name or forwards names to a call."""
    if len(body) != 1 or not isinstance(body[0], ast.Return | ast.Expr) or body[0].value is None:
        return False
    value = body[0].value
    plain = (ast.Name, ast.Attribute, ast.Constant)
    if isinstance(value, ast.Call):
        args = [*value.args, *(k.value for k in value.keywords)]
        return isinstance(value.func, plain) and all(isinstance(a, plain) for a in args)
    return isinstance(value, plain)


def _canon(node: object) -> str:
    """The tree as text, empty fields left out, so the hash is the same on every Python.

    ast.dump's output changes between versions, and a baseline written on one
    would name no group found on another.
    """
    if isinstance(node, ast.AST):
        fields = ((name, getattr(node, name, None)) for name in node._fields)
        inner = ",".join(
            f"{name}={_canon(value)}" for name, value in fields if value not in (None, [])
        )
        return f"{type(node).__name__}({inner})"
    if isinstance(node, list):
        return "[" + ",".join(_canon(item) for item in node) + "]"
    return repr(node)


def functions(text: str) -> list[tuple[str, str, int, bool]]:
    """(name, digest, normalised lines, is a delegate) per function; the def line counts once."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    _Strip().visit(tree)
    lines = text.splitlines()
    found = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        body = _body(fn)
        if not body:
            continue
        span = lines[body[0].lineno - 1 : fn.end_lineno]
        size = 1 + sum(1 for ln in span if ln.strip() and not ln.strip().startswith("#"))
        dump = _canon(fn.args) + "".join(_canon(node) for node in body)
        digest = hashlib.sha256(dump.encode()).hexdigest()
        found.append((fn.name, digest, size, _delegate(body)))
    return found


def _shell_lines(text: str) -> list[tuple[str, bool, int]]:
    """(line with its comment cut, starts outside quotes and heredocs, brace depth after).

    Braces inside quotes and heredocs do not count; a heredoc line is kept whole.
    """
    out: list[tuple[str, bool, int]] = []
    quote, pending, ending, depth = "", [], "", 0
    for raw in text.splitlines():
        if ending:
            if raw.strip() == ending:
                ending = pending.pop(0) if pending else ""
            out.append((raw, False, depth))
            continue
        live = not quote
        cut, quote, delta = _lex_line(raw, quote, pending)
        depth += delta
        out.append((raw[:cut], live, depth))
        if pending and not quote:
            ending = pending.pop(0)
    return out


def _lex_line(raw: str, quote: str, pending: list[str]) -> tuple[int, str, int]:
    """(where the comment starts, open quote at the end, brace delta); queues heredoc ends."""
    i, delta = 0, 0
    while i < len(raw):
        c = raw[i]
        if quote:
            if c == quote:
                quote = ""
            elif c == "\\" and quote == '"':
                i += 1
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "#" and (i == 0 or raw[i - 1] in " \t;&|("):
            break
        elif raw.startswith("<<<", i):
            i += 2
        elif m := SH_HEREDOC.match(raw, i):
            pending.append(m.group(2))
            i = m.end() - 1
        elif c in "{}":
            delta += 1 if c == "{" else -1
        i += 1
    return i, quote, delta


def shell_functions(text: str) -> list[tuple[str, str, int, bool]]:
    """Same shape as functions(); a one-line ``f() { ...; }`` body is one line."""
    lines = _shell_lines(text)
    found, before = [], 0
    for n, (code, live, depth) in enumerate(lines):
        m = SH_DEF.match(code) if live else None
        body = None
        if m and m.group(4) is not None and depth == before:
            body = [m.group(4).strip()]
        elif m and m.group(4) is None and depth == before + 1:
            body = _shell_body(lines[n + 1 :], before, m.group(1))
        before = depth
        if m and body:
            digest = hashlib.sha256("\n".join(body).encode()).hexdigest()
            trivial = len(body) == 1 and not SH_COMPOUND.search(body[0])
            found.append((m.group(2) or m.group(3), digest, 1 + len(body), trivial))
    return found


def _shell_body(rest: list[tuple[str, bool, int]], depth: int, indent: str) -> list[str] | None:
    """Stripped non-blank body lines, or None unless a lone ``}`` at the def's indent ends it."""
    for end, (code, live, after) in enumerate(rest):
        if after > depth:
            continue
        if not (live and after == depth and code.rstrip() == indent + "}"):
            return None
        return [s for s in (ln[0].strip() for ln in rest[:end]) if s]
    return None


def _language(path: str, text: str) -> str | None:
    name = path.rsplit("/", 1)[-1]
    if name.endswith(".py"):
        return "py"
    if name.endswith(".sh") or ("." not in name and re.match(SH_SHEBANG, text)):
        return "sh"
    return None


Scan = tuple[dict[str, int], dict[str, list[str]], set[str], dict[str, list[str]]]


def scan(root: Path) -> Scan:
    """Source files over the ceiling, source duplicate groups, the shell keys, test groups."""
    sizes: dict[str, int] = {}
    by_kind: dict[str, dict[str, list[str]]] = {
        "source": defaultdict(list),
        "test": defaultdict(list),
    }
    shell: set[str] = set()
    for path in list_files(root):
        which = kind(path, root)
        if which is None:
            continue
        groups = by_kind[which]
        text = _read(root / path)
        count = len(text.splitlines())
        if which == "source" and count > CEILING:
            sizes[path] = count
        lang = _language(path, text)
        if lang is None:
            continue
        for name, digest, size, delegate in (shell_functions if lang == "sh" else functions)(text):
            if size < MIN_LINES and delegate:
                continue
            key = digest if size >= MIN_LINES else f"{name}:{digest}"
            # Python keys stay unprefixed so the existing baseline hashes do not move.
            key = "sh:" + key if lang == "sh" else key
            group = hashlib.sha256(key.encode()).hexdigest()[:16]
            groups[group].append(f"{path}:{name}")
            if lang == "sh" and which == "source":
                shell.add(group)
    dups, test_dups = (
        {k: sorted(v) for k, v in by_kind[which].items() if len(_files(v)) > 1}
        for which in ("source", "test")
    )
    return sizes, dups, shell & dups.keys(), test_dups


def _files(members: list[str]) -> set[str]:
    return {m.rsplit(":", 1)[0] for m in members}


def check(
    sizes: dict[str, int],
    dups: dict[str, list[str]],
    test_dups: dict[str, list[str]],
    baseline: dict,
) -> list[str]:
    errors = []
    allowed = baseline.get("sizes", {})
    exempt = baseline.get("exempt", {})
    errors += [
        f"{path}: exempt from the size rule with no reason. Say why in {BASELINE}."
        for path, reason in sorted(exempt.items())
        if not str(reason).strip()
    ]
    for path, count in sorted(sizes.items()):
        if path in exempt:
            continue
        if path not in allowed:
            errors.append(f"{path}: {count} lines, over the {CEILING}-line ceiling. Split it.")
        elif count > allowed[path]:
            errors.append(
                f"{path}: grew to {count} lines; the baseline allows {allowed[path]}."
                " A file over the ceiling may only shrink."
            )
    for section, found, where in (
        ("duplicates", dups, "file"),
        ("test_duplicates", test_dups, "test file"),
    ):
        known = baseline.get(section, {})
        for key, members in sorted(found.items(), key=lambda kv: kv[1]):
            if key in known and len(_files(members)) <= len(_files(known[key])):
                continue
            errors.append(
                f"the same function body is defined in more than one {where}; import one copy"
                f" instead (group {key}): " + ", ".join(members)
            )
    return errors


def added_lines(root: Path, base: str) -> dict[str, list[tuple[int, str]]]:
    """Added (line number, text) per file between the merge base and HEAD."""
    merge_base = _git(root, "merge-base", base, "HEAD").strip()
    out = _git(root, "diff", "-U0", "--no-color", "--no-renames", merge_base, "HEAD")
    added: dict[str, list[tuple[int, str]]] = defaultdict(list)
    path, line = "", 0
    for raw in out.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else ""
        elif m := HUNK.match(raw):
            line = int(m.group(1))
        elif raw.startswith("+") and path:
            added[path].append((line, raw[1:]))
            line += 1
    return added


def _docstring_lines(text: str) -> set[int]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                if isinstance(first.value.value, str) and first.end_lineno:
                    lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def _is_comment(path: str, text: str) -> bool:
    s = text.strip()
    if path.endswith((".ts", ".tsx", ".mjs")):
        return s.startswith(("//", "/*", "*"))
    return s.startswith("#")


def diff_metrics(root: Path, base: str) -> dict[str, str]:
    counts = {"source": 0, "test": 0, "comment": 0, "code": 0}
    for path, lines in added_lines(root, base).items():
        which = kind(path, root)
        if which is None or not (root / path).is_file():
            continue
        docs = _docstring_lines(_read(root / path)) if path.endswith(".py") else set()
        for number, text in lines:
            if not text.strip():
                continue
            counts[which] += 1
            counts["code"] += 1
            if number in docs or _is_comment(path, text):
                counts["comment"] += 1
    share = counts["comment"] / counts["code"] if counts["code"] else 0.0
    ratio = counts["test"] / counts["source"] if counts["source"] else 0.0
    return {
        "added_source_lines": str(counts["source"]),
        "added_test_lines": str(counts["test"]),
        "comment_share": f"{share:.3f}",
        "test_to_product": f"{ratio:.3f}",
    }


def main(argv: list[str] | None = None) -> int:
    if sys.version_info < MIN_PYTHON:
        # An older parser cannot read this code, and an unread file has no functions.
        print(f"check_code_ratchet needs Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer.")
        return 2
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=REPO_ROOT)
    ap.add_argument("--base", help="PR base ref or sha; enables the diff numbers")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true", help="print key=value lines, exit 0")
    mode.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args(argv)
    root: Path = args.root.resolve()
    sizes, dups, shell, test_dups = scan(root)
    groups = (
        f"{len(dups) - len(shell)} Python, {len(shell)} shell"
        f" and {len(test_dups)} test duplicate groups"
    )
    baseline_path = root / BASELINE
    if args.write_baseline:
        exempt = (
            json.loads(_read(baseline_path)).get("exempt", {}) if baseline_path.is_file() else {}
        )
        sizes = {path: count for path, count in sizes.items() if path not in exempt}
        data = {
            "ceiling": CEILING,
            "exempt": exempt,
            "sizes": sizes,
            "duplicates": dups,
            "test_duplicates": test_dups,
        }
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", newline="\n")
        print(f"wrote {BASELINE}: {len(sizes)} files over {CEILING}, {groups}")
        return 0
    baseline = json.loads(_read(baseline_path)) if baseline_path.is_file() else {}
    numbers = {
        "files_over_ceiling": str(len(sizes)),
        "duplicate_groups": str(len(dups) - len(shell)),
        "shell_duplicate_groups": str(len(shell)),
        "test_duplicate_groups": str(len(test_dups)),
    }
    if args.base:
        numbers |= diff_metrics(root, args.base)
    if args.report:
        for key, value in numbers.items():
            print(f"{key}={value}")
        return 0
    print("::notice title=Code ratchet::" + " ".join(f"{k}={v}" for k, v in numbers.items()))
    if int(numbers.get("added_source_lines", 0)) > LARGE_DIFF:
        print(
            f"::warning title=Large diff::{numbers['added_source_lines']} added non-test source"
            f" lines (over {LARGE_DIFF}). A diff this size should get a human look."
        )
    errors = check(sizes, dups, test_dups, baseline)
    for error in errors:
        print(f"::error title=Code ratchet::{error}")
    if errors:
        print(f"{len(errors)} code ratchet failure(s). See {BASELINE} for what is grandfathered.")
        return 1
    print(f"code ratchet OK: {len(sizes)} grandfathered large files, {groups} known")
    return 0


if __name__ == "__main__":
    sys.exit(main())
