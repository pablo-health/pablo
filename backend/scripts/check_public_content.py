#!/usr/bin/env python3
"""Guard the two things that must never land in this repository.

    python3 backend/scripts/check_public_content.py --diff-base origin/main

This repository is public. A merge here cannot be undone, and rewriting
published history is worse than whatever it would hide. So a small number of
strings are checked mechanically on every change.

DELIBERATELY NARROW. Only patterns that are unambiguous and that the tree is
already clean of are enforced, because a check that fires against established
practice gets muted, and a muted check is worse than none — it still reports
green. Judgement calls about wording belong in review, where a human can weigh
them; this file only catches the things that are never right.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys

# Assembled from fragments rather than written out, because a checker that
# contains the literal string it forbids FLAGS ITS OWN SOURCE the moment that
# source is added — which is exactly what happened on the first run.
_PRIVATE_REPO_PREFIX = "pablo" + "-" + "saas:"

FORBIDDEN: tuple[tuple[str, str], ...] = (
    # Internal repository paths. A comment that explains a seam by pointing at
    # another repository's file path is describing plumbing the reader cannot
    # open — say what the contract IS instead.
    (re.escape(_PRIVATE_REPO_PREFIX), "internal repository path"),
    # Commits here are the author's work, tool-assisted.
    (r"Co-Authored-By:\s*Claude", "AI attribution"),
    (r"Generated with \[?Claude", "AI attribution"),
)


def scan(text: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for pattern, label in FORBIDDEN:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            out.append((label, m.group(0)))
    return out


def _git(args: list[str]) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--diff-base", required=True)
    args = ap.parse_args()

    problems: list[str] = []
    # The diff, and the commit messages: a squash-merge publishes those too.
    for source, text in (
        ("diff", _git(["diff", f"{args.diff_base}...HEAD"])),
        ("commit message", _git(["log", "--format=%B", f"{args.diff_base}..HEAD"])),
    ):
        for label, hit in scan(text):
            problems.append(f"  {source}: {label} ({hit!r})")

    if not problems:
        # An EMPTY diff is not proof of cleanliness — it means nothing was
        # inspected. Say which, so a misconfigured base ref cannot read as a
        # pass. (Found by running this against uncommitted work, where
        # `git diff <base>...HEAD` compares committed refs and sees nothing.)
        if not _git(["diff", "--name-only", f"{args.diff_base}...HEAD"]).strip():
            print(f"public-content check: NOTHING TO INSPECT (no diff vs {args.diff_base})")
            return 0
        print("public-content check ok")
        return 0
    print("PUBLIC CONTENT CHECK FAILED", file=sys.stderr)
    for p in dict.fromkeys(problems):
        print(p, file=sys.stderr)
    print("\nThis repository is public and a merge cannot be undone.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
