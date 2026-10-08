#!/usr/bin/env bash
# Ask the model what each chart-proposal case changes on the chart, and grade it.
#
# The proposal call goes to Vertex, so this needs application default
# credentials and a project with Vertex access:
#   gcloud auth application-default login
#   export GOOGLE_CLOUD_PROJECT=<your project>
#   export GOOGLE_CLOUD_LOCATION=global GOOGLE_GENAI_USE_VERTEXAI=true
#
# Usage:
#   scripts/run-chart-proposal-eval.sh              # every case
#   scripts/run-chart-proposal-eval.sh --case unchanged
#   scripts/run-chart-proposal-eval.sh --runs 3
#   scripts/run-chart-proposal-eval.sh --json       # proposals included

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"

# Prefer the project's own environment: a bare python3 is often an older,
# unrelated interpreter that fails on syntax before anything runs.
PYTHON="${PABLO_PYTHON:-}"
if [[ -z "${PYTHON}" ]]; then
    if VENV="$(cd "${REPO_ROOT}" && poetry env info --path 2>/dev/null)" && [[ -x "${VENV}/bin/python" ]]; then
        PYTHON="${VENV}/bin/python"
    else
        PYTHON="python3"
    fi
fi

if ! "${PYTHON}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 13) else 1)'; then
    echo "This eval needs Python 3.13+, but ${PYTHON} is $("${PYTHON}" -V 2>&1)." >&2
    echo "Run 'poetry install' in ${REPO_ROOT}, or set PABLO_PYTHON to a 3.13 interpreter." >&2
    exit 2
fi

if ! "${PYTHON}" -c 'import pydantic' >/dev/null 2>&1; then
    echo "${PYTHON} has no backend dependencies installed." >&2
    echo "Run 'poetry install' in ${REPO_ROOT}, or set PABLO_PYTHON to an" >&2
    echo "interpreter that already has them (e.g. the main checkout's venv)." >&2
    exit 2
fi

exec "${PYTHON}" -m evals.chart_proposals.run "$@"
