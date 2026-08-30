#!/usr/bin/env bash
# Grade the natural-language availability-rule parser against its corpus.
#
# The parser calls Vertex, so this needs application default credentials
# and a project with Vertex access:
#   gcloud auth application-default login
#   export GOOGLE_CLOUD_PROJECT=pablohealth-dev
#
# Usage:
#   scripts/run-availability-parse-eval.sh              # the whole corpus
#   scripts/run-availability-parse-eval.sh --list       # show the cases
#   scripts/run-availability-parse-eval.sh --case friday
#   scripts/run-availability-parse-eval.sh --json

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="${REPO_ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"

# The backend is 3.13 and this code uses syntax older interpreters cannot
# parse, so a bare `python3` — which on a developer machine is often an
# unrelated system or conda install — fails with a bewildering SyntaxError
# before anything runs. Prefer the project's own environment, and say so
# plainly rather than letting the parser error stand in for the message.
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

# A 3.13 interpreter with nothing installed in it is the other way this goes
# wrong — an empty in-project venv resolves fine and then the first parse
# dies on a missing import, which reads like a credentials problem and is
# not one. Check for the backend's dependencies here, where the message can
# say what to actually do.
if ! "${PYTHON}" -c 'import pydantic' >/dev/null 2>&1; then
    echo "${PYTHON} has no backend dependencies installed." >&2
    echo "Run 'poetry install' in ${REPO_ROOT}, or set PABLO_PYTHON to an" >&2
    echo "interpreter that already has them (e.g. the main checkout's venv)." >&2
    exit 2
fi

exec "${PYTHON}" -m evals.availability_parse.run "$@"
