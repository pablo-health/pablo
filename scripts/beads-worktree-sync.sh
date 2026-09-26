#!/usr/bin/env bash
# Bring a git worktree's beads configuration in line with the main checkout.
#
# The committed .beads/metadata.json selects dolt_mode=embedded: a private,
# per-checkout database seeded from the committed issues.jsonl. A developer
# who works against a shared beads server carries a different metadata.json
# and config.yaml in the main checkout, hidden from `git status` with
# `git update-index --skip-worktree` so the override never gets committed.
#
# Skip-worktree is an index flag, and every worktree has its own index. A
# worktree created with `git worktree add` therefore starts from the
# committed files, and the beads post-checkout hook seeds an embedded
# database from the jsonl snapshot it finds there. `bd` inside that worktree
# then reads a stale copy of the board, and anything it writes (a close, a
# status change) lands in that private database and never reaches the
# shared server.
#
# This script copies the main checkout's actual metadata.json and
# config.yaml into the worktree and re-applies skip-worktree there, so the
# worktree talks to the same beads database the main checkout does. Run
# from the main checkout it does nothing and exits 0.
set -eu

git_dir_raw="$(git rev-parse --git-dir 2>/dev/null || true)"
common_dir_raw="$(git rev-parse --git-common-dir 2>/dev/null || true)"

if [ -z "$git_dir_raw" ] || [ -z "$common_dir_raw" ]; then
  exit 0
fi

git_dir="$(cd "$git_dir_raw" && pwd)"
common_dir="$(cd "$common_dir_raw" && pwd)"

if [ "$git_dir" = "$common_dir" ]; then
  # Main checkout, not a worktree: it is the source of truth, nothing to sync.
  exit 0
fi

main_checkout="$(dirname "$common_dir")"
main_beads="$main_checkout/.beads"
worktree_root="$(git rev-parse --show-toplevel)"
worktree_beads="$worktree_root/.beads"

# Nothing to mirror if the main checkout has no beads state of its own yet.
if [ ! -f "$main_beads/metadata.json" ]; then
  exit 0
fi

cp -f "$main_beads/metadata.json" "$worktree_beads/metadata.json"
if [ -f "$main_beads/config.yaml" ]; then
  cp -f "$main_beads/config.yaml" "$worktree_beads/config.yaml"
fi

# Match the main checkout: these two files carry a local override and
# should never show up as modified in `git status`.
git -C "$worktree_root" update-index --skip-worktree \
  .beads/metadata.json .beads/config.yaml 2>/dev/null || true
