#!/usr/bin/env python3
"""Guard the few things that must never land in this repository.

    python3 backend/scripts/check_public_content.py --diff-base origin/main

This repository is public. A merge here cannot be undone, and rewriting
published history is worse than whatever it would hide. So a small number of
strings are checked mechanically on every change.

DELIBERATELY NARROW. Only patterns that are unambiguous, and that the tree is
already clean of, are enforced. A check that fires against established practice
gets muted, and a muted check is worse than none because it still reports green.
Judgement about wording belongs in review, where a human can weigh it; this file
only catches what is never right.

Three details below look like fussiness and are not. Each was found by running
the check, not by reading it:

  * SCANS ADDED LINES ONLY. A diff contains removals too, so scanning it whole
    means the commit that DELETES a forbidden string fails for containing it —
    the fix for a leak rejected on the grounds that it mentions the leak.
  * PASSES --text TO git diff. One stray control byte makes git classify a file
    as binary and print "Binary files ... differ" instead of content, leaving a
    diff-based text check blind to that file in BOTH directions. That is exactly
    how the reference removed alongside this file survived: a single NUL byte hid
    it from every text search and every diff.
  * PATTERNS ARE ASSEMBLED FROM FRAGMENTS. A checker that spells out the string
    it forbids flags its own source the moment that source is added.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Assembled, not spelled out — see the module docstring.
_OTHER_REPO_PREFIX = "pablo" + "-" + "saas:"
_ASSISTANT = "Cla" + "ude"

FORBIDDEN: tuple[tuple[str, str], ...] = (
    # A comment that explains a seam by pointing at a file path in another
    # repository describes plumbing the reader cannot open. Say what the
    # contract IS instead — it reads better for everyone working in this tree.
    (re.escape(_OTHER_REPO_PREFIX), "path into another repository"),
    # Commits here are the author's work, tool-assisted.
    (r"Co-Authored-By:\s*" + _ASSISTANT, "AI attribution"),
    (r"Generated with \[?" + _ASSISTANT, "AI attribution"),
)


def scan(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for pattern, label in FORBIDDEN:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            found.append((label, m.group(0)))
    return found


def added_lines(diff: str) -> str:
    """Only the lines a change INTRODUCES."""
    return "\n".join(
        ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")
    )


def _git(args: list[str]) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diff-base", required=True)
    args = ap.parse_args()
    base = args.diff_base

    # An EMPTY diff is not proof of cleanliness — it means nothing was
    # inspected. Say which, so a misconfigured base ref cannot read as a pass.
    if not _git(["diff", "--name-only", f"{base}...HEAD"]).strip():
        print(f"public-content check: NOTHING TO INSPECT (no diff vs {base})")
        return 0

    problems: list[str] = []
    sources = (
        ("diff", added_lines(_git(["diff", "--text", f"{base}...HEAD"]))),
        # A squash-merge publishes the branch's commit messages too.
        ("commit message", _git(["log", "--format=%B", f"{base}..HEAD"])),
    )
    for source, text in sources:
        for label, hit in scan(text):
            problems.append(f"  {source}: {label} ({hit!r})")

    if not problems:
        print("public-content check ok")
        return 0

    print("PUBLIC CONTENT CHECK FAILED", file=sys.stderr)
    for p in dict.fromkeys(problems):
        print(p, file=sys.stderr)
    print("\nThis repository is public and a merge cannot be undone.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
