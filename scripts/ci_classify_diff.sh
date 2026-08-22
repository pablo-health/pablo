#!/usr/bin/env bash
#
# Classify a pull request's diff so CI jobs can skip the WORK without
# skipping the CHECK.
#
# Why this exists: `paths-ignore` suppresses the whole workflow run, so the
# checks it owns never report at all. When those checks are also required by
# branch protection, the pull request waits forever for a result that is
# never coming, and the only way out is an admin override. Skipping work is a
# cost decision; skipping a required check is a merge policy, and the two
# should not be the same switch.
#
# So every job runs on every pull request and this script tells it how much
# work is warranted. A job with nothing to do still exits 0 and reports green.
#
# Usage:  scripts/ci_classify_diff.sh <base-ref> [head-ref]
#
# Emits `key=value` on stdout, and appends to $GITHUB_OUTPUT under GitHub
# Actions. Keys:
#
#   substantive — something outside the ignorable set changed, so the full
#                 job is warranted. This is the only key jobs should gate on.
#
# Fails open: an empty or unresolvable diff classifies as substantive=true,
# so a detection miss degrades to "run everything" rather than "skip
# something that mattered".
set -euo pipefail

base_ref="${1:?usage: ci_classify_diff.sh <base-ref> [head-ref]}"
head_ref="${2:-HEAD}"

changed="$(git diff --name-only "${base_ref}...${head_ref}" || true)"
echo "Changed files vs ${base_ref}:" >&2
echo "${changed}" >&2

# Deliberately narrow: the issue-tracker export is written by tooling and
# cannot affect lint, types, migrations, the image, or any test. Widening
# this set trades CI minutes for the risk of skipping a check that mattered,
# so add to it only with a reason per entry.
ignorable='^\.beads/'

if [ -z "${changed}" ]; then
  substantive=true
else
  remainder="$(echo "${changed}" | grep -vE "${ignorable}" || true)"
  [ -n "${remainder}" ] && substantive=true || substantive=false
fi

line="substantive=${substantive}"
echo "${line}"
[ -n "${GITHUB_OUTPUT:-}" ] && echo "${line}" >>"${GITHUB_OUTPUT}"
exit 0
