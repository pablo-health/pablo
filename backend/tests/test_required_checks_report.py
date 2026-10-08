# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""A required check must be able to report on every pull request.

A workflow suppressed by a ``paths:`` filter does not report a skipped result.
It reports *nothing*, and branch protection waits for a run that will never
start. The pull request is then unmergeable rather than merely untested, and
the only exits are an admin override or an unrelated commit that happens to
match one of the listed paths.

This has now happened twice on ``End-to-end (compose + Playwright)``:

* **#1288** changed ``backend/app/models/validators.py``. Nineteen checks
  reported green; the twentieth never started. Fixed at the time by adding
  ``backend/app/models/**`` to the filter — which fixed that directory and
  left the class intact.
* **#1294** was a beads export: one line of ``.beads/issues.jsonl``. Auto-merge
  armed, every reported check green, blocked for a day with nothing to arm
  against.

The lesson the second one forced is that widening the list is not the fix. The
list *is* the fix's problem: every directory absent from it is a merge deadlock
waiting for its first pull request. So the filter is gone, and
``scripts/ci_classify_diff.sh`` decides how much WORK is warranted while the
check always reports — the separation ``ci.yml`` already makes in four jobs.

Skipping work is a cost decision. Skipping a required check is a merge policy.
They should not be the same switch.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml

WORKFLOWS = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows"
E2E = WORKFLOWS / "e2e.yml"
E2E_BAKE = WORKFLOWS.parents[1] / "docker-bake.e2e.hcl"


def _triggers(doc: dict) -> dict:
    # PyYAML resolves a bare `on:` key to the boolean True.
    return doc.get("on") or doc.get(True) or {}


@pytest.fixture(scope="module")
def e2e() -> dict:
    return yaml.safe_load(E2E.read_text())


def test_e2e_has_no_pull_request_paths_filter(e2e: dict) -> None:
    """The regression itself. This job owns a required check."""
    pr = _triggers(e2e).get("pull_request") or {}
    assert "paths" not in pr, (
        "End-to-end owns a required check, so a paths filter makes every "
        "unlisted directory an unmergeable pull request. Gate the WORK with "
        "scripts/ci_classify_diff.sh instead."
    )
    assert "paths-ignore" not in pr


def test_e2e_still_runs_on_pull_requests(e2e: dict) -> None:
    """Removing the filter must not remove the trigger."""
    assert "pull_request" in _triggers(e2e)


def test_e2e_gates_its_work_on_the_classifier(e2e: dict) -> None:
    classify = e2e["jobs"]["classify"]
    steps = classify["steps"]
    assert any("ci_classify_diff.sh" in str(s.get("run", "")) for s in steps), (
        "no classify step — without it, dropping the paths filter just makes "
        "every docs-only PR pay for the full browser suite"
    )

    for name in ("e2e-shards", "hosted-address"):
        job = e2e["jobs"][name]
        assert job["needs"] == "classify"
        assert "needs.classify.outputs.run == 'true'" in job["if"]

    gate = e2e["jobs"]["e2e"]
    assert gate["if"] == "always()"
    assert set(gate["needs"]) == {"classify", "e2e-shards", "hosted-address"}


def test_the_gate_fails_open(e2e: dict) -> None:
    """A skipped classifier step must still run release and scheduled suites."""
    result = next(step for step in e2e["jobs"]["classify"]["steps"] if step.get("id") == "result")
    script = str(result["run"])
    assert 'if [[ "$SUBSTANTIVE" == "false" ]]' in script
    assert 'echo "run=true"' in script


def test_the_classifier_ignores_the_beads_export() -> None:
    """#1294's diff, and the reason the export is safe to ignore: it is written
    by tooling and cannot affect lint, types, migrations, the image or a test."""
    script = (WORKFLOWS.parents[1] / "scripts" / "ci_classify_diff.sh").read_text()
    assert r"^\.beads/" in script


def test_e2e_builds_with_an_isolated_github_actions_cache(e2e: dict) -> None:
    """Parallel lanes share per-image cache entries instead of one global scope."""
    for job_name in ("e2e-shards", "hosted-address"):
        steps = e2e["jobs"][job_name]["steps"]
        assert any("docker/setup-buildx-action@" in step.get("uses", "") for step in steps)
        assert any("docker/bake-action@" in step.get("uses", "") for step in steps)
        start = next(step for step in steps if step.get("name", "").startswith("Start the"))
        assert "--no-build" in start["run"]

    bake = E2E_BAKE.read_text()
    scopes = re.findall(r"cache-to = \[\"type=gha,mode=max,scope=([^\"]+)", bake)
    assert len(scopes) == len(set(scopes))
    assert len(scopes) >= 2
