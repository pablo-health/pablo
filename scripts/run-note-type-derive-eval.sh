#!/usr/bin/env bash
# Grade note-type derive (proposal, coverage, copied-text guard) against its cases.
#
# Derive calls Vertex, so this needs application default credentials and a
# project with Vertex access:
#   gcloud auth application-default login
#   export GOOGLE_CLOUD_PROJECT=<your project>
#
# Usage:
#   scripts/run-note-type-derive-eval.sh              # every case
#   scripts/run-note-type-derive-eval.sh --list       # show the cases
#   scripts/run-note-type-derive-eval.sh --case dap
#   scripts/run-note-type-derive-eval.sh --json
#   scripts/run-note-type-derive-eval.sh --model bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0

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

exec "${PYTHON}" -m evals.note_type_derive.run "$@"
