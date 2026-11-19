# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""The VM fetches its deploy files at any ref: a branch, a tag or a commit SHA.

``deploy/vm/boot-stub.sh`` and ``deploy/vm/bin/pablo-update`` each carry the
same ``fetch_release`` shell function. These tests lift it out of both files and
run it against a local bare repository, so a deploy pinned to a commit (which
``git clone --branch`` can never fetch) is proved to work.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = (
    REPO_ROOT / "deploy" / "vm" / "boot-stub.sh",
    REPO_ROOT / "deploy" / "vm" / "bin" / "pablo-update",
)

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or shutil.which("bash") is None,
    reason="needs git and bash",
)

# A hermetic git: no user or system config, a fixed author.
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _function(script: Path) -> str:
    match = re.search(r"^fetch_release\(\) \{\n.*?^\}\n", script.read_text(), re.M | re.S)
    assert match, f"no fetch_release in {script}"
    return match.group(0)


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(  # noqa: S603 — fixed argv, git on PATH, no shell
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit(work: Path, content: str) -> str:
    (work / "deploy" / "vm" / "marker").write_text(content)
    _git("add", "-A", cwd=work)
    _git("commit", "-q", "-m", content, cwd=work)
    return _git("rev-parse", "HEAD", cwd=work)


def _push(work: Path) -> None:
    _git("push", "-q", "--tags", "origin", "main", cwd=work)


@pytest.fixture
def origin(tmp_path: Path) -> tuple[str, Path]:
    """A bare origin with deploy/vm, backend/bin and a path outside both."""
    bare = tmp_path / "origin.git"
    _git("init", "-q", "--bare", "-b", "main", str(bare), cwd=tmp_path)
    _git("config", "uploadpack.allowFilter", "true", cwd=bare)
    work = tmp_path / "work"
    _git("clone", "-q", str(bare), str(work), cwd=tmp_path)
    _git("symbolic-ref", "HEAD", "refs/heads/main", cwd=work)
    for path in ("deploy/vm/bootstrap.sh", "backend/bin/allow_email.py", "frontend/app.ts"):
        (work / path).parent.mkdir(parents=True, exist_ok=True)
        (work / path).write_text(path)
    return f"file://{bare}", work


def _fetch(script: Path, url: str, ref: str, dest: Path) -> None:
    program = f'set -euo pipefail\n{_function(script)}\nfetch_release "$@"'
    subprocess.run(  # noqa: S603 — our own script's function, arguments passed as argv
        ["bash", "-c", program, "_", url, ref, str(dest)],  # noqa: S607
        env=GIT_ENV,
        check=True,
        capture_output=True,
        text=True,
    )


def _checked_out(dest: Path) -> tuple[str, str]:
    return _git("rev-parse", "HEAD", cwd=dest), (dest / "deploy/vm/marker").read_text()


def test_both_scripts_carry_the_same_function() -> None:
    first, second = (_function(script) for script in SCRIPTS)
    assert first == second


def test_neither_script_clones_by_branch() -> None:
    for script in SCRIPTS:
        assert "git clone" not in script.read_text(), script


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
@pytest.mark.parametrize("kind", ["branch", "tag", "sha"])
def test_fetches_a_branch_a_tag_and_a_commit(
    script: Path, kind: str, origin: tuple[str, Path], tmp_path: Path
) -> None:
    url, work = origin
    old = _commit(work, "old")
    _git("tag", "-a", "v1.2.3", "-m", "release", cwd=work)
    new = _commit(work, "new")
    _push(work)

    # The branch is at its tip; the tag and the SHA are one commit behind it.
    ref = {"branch": "main", "tag": "v1.2.3", "sha": old}[kind]
    want = (new, "new") if kind == "branch" else (old, "old")
    assert kind != "sha" or re.fullmatch(r"[0-9a-f]{40}", ref)
    dest = tmp_path / "release"
    _fetch(script, url, ref, dest)

    assert _checked_out(dest) == want
    assert (dest / "deploy/vm/bootstrap.sh").is_file()
    assert (dest / "backend/bin/allow_email.py").is_file()
    assert not (dest / "frontend").exists()


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
def test_a_branch_moves_and_a_tag_does_not(
    script: Path, origin: tuple[str, Path], tmp_path: Path
) -> None:
    url, work = origin
    first = _commit(work, "first")
    _git("tag", "v1.0.0", cwd=work)
    _push(work)
    branch_dir, tag_dir = tmp_path / "main", tmp_path / "v1.0.0"
    _fetch(script, url, "main", branch_dir)
    _fetch(script, url, "v1.0.0", tag_dir)

    second = _commit(work, "second")
    _git("tag", "-f", "v1.0.0", cwd=work)
    _git("push", "-q", "-f", "--tags", "origin", "main", cwd=work)
    _fetch(script, url, "main", branch_dir)
    _fetch(script, url, "v1.0.0", tag_dir)

    assert _checked_out(branch_dir) == (second, "second")
    assert _checked_out(tag_dir) == (first, "first")


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.name)
def test_an_interrupted_first_fetch_heals(
    script: Path, origin: tuple[str, Path], tmp_path: Path
) -> None:
    url, work = origin
    sha = _commit(work, "only")
    _git("tag", "v2.0.0", cwd=work)
    _push(work)
    dest = tmp_path / "v2.0.0"
    _git("init", "-q", str(dest), cwd=tmp_path)  # stopped before the remote was added

    _fetch(script, url, "v2.0.0", dest)

    assert _checked_out(dest) == (sha, "only")
