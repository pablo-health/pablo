#!/usr/bin/env bash
# Rate-limit-safe sweep: SOAP faithfulness + latency across thinking budgets.
# Answers "does the thinking budget need to scale with transcript length, and
# where does a fixed cap start truncating?" — paced with long sleeps so Vertex
# dev quota (429) doesn't pollute the latency numbers.
#
# Usage: bash backend/evals/sweep_thinking_budget.sh 2>&1 | tee /tmp/sweep.log
set -uo pipefail

PY=/Users/kurtn/Library/Caches/pypoetry/virtualenvs/pablo-YtzK5q4a-py3.13/bin/python
export GOOGLE_CLOUD_PROJECT=pablohealth-dev
export GOOGLE_CLOUD_LOCATION=global
export NOTE_GENERATION_TEMPERATURE=0.0   # hold temp fixed; isolate thinking budget
cd "$(dirname "$0")/../.." || exit 1     # -> backend/.. = repo worktree root

SLEEP_BETWEEN=100                        # seconds between calls (quota-safe)
CASES=(note-faith-013 note-faith-016)    # 013 = long/complex, 016 = flaky (AUD inflation)
BUDGETS=(uncapped 8192 4096 2048)

echo "sweep start | temp=0.0 | cases=${CASES[*]} | budgets=${BUDGETS[*]}"
for budget in "${BUDGETS[@]}"; do
  for case in "${CASES[@]}"; do
    if [ "$budget" = "uncapped" ]; then unset NOTE_THINKING_BUDGET; else export NOTE_THINKING_BUDGET="$budget"; fi
    line=$(PYTHONPATH=. "$PY" -m backend.evals.run_note_generation --case "$case" 2>/dev/null \
             | grep -E "generated [0-9]+ chars|\[PASS\]|\[FAIL\]" | tr '\n' ' ')
    echo "budget=$budget case=$case :: $line"
    sleep "$SLEEP_BETWEEN"
  done
done
echo "sweep done"
