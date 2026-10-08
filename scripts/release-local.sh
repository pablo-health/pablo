#!/usr/bin/env bash
# Publish the release images from a workstation, for when hosted CI can't run.
#
# The Release workflow builds backend, frontend and pentest images on every
# push to main and publishes them to ghcr.io as :<sha> and :main. If GitHub's
# hosted runners are unavailable, a merged change can't produce images, and
# anything built on top of them waits. This script builds the same three
# images, from the same Dockerfiles, at a commit already on origin/main, and
# pushes them with the same tags.
#
# It is an escape hatch, not a second release path:
#   - It only builds commits on origin/main. Merge the change first.
#   - It never overwrites an existing :<sha> tag. If the Release workflow
#     already published that commit, there is nothing to do.
#   - It skips the e2e job the workflow runs first. Run `make check` (and the
#     e2e suite if the change warrants it) before you publish.
#
# Usage:
#   scripts/release-local.sh [<ref>]     default: origin/main
#
# Needs: docker with buildx, git, and gh logged in with the write:packages
# scope (gh auth refresh -s write:packages).
set -euo pipefail

REGISTRY=ghcr.io/pablo-health
# GitHub-hosted runners are amd64; a workstation may not be.
PLATFORM=linux/amd64
REF="${1:-origin/main}"

die() { echo "ERROR: $*" >&2; exit 1; }
say() { printf '\n=== %s\n' "$*"; }

ROOT="$(git rev-parse --show-toplevel)"
git -C "$ROOT" fetch -q origin main
SHA="$(git -C "$ROOT" rev-parse --verify "$REF^{commit}")" || die "can't resolve $REF"
git -C "$ROOT" merge-base --is-ancestor "$SHA" origin/main \
  || die "$SHA is not on origin/main; merge the change first"
MAIN_SHA="$(git -C "$ROOT" rev-parse origin/main)"

command -v docker >/dev/null || die "docker is required"
docker buildx version >/dev/null 2>&1 || die "docker buildx is required"

for image in backend frontend pentest; do
  if docker manifest inspect "$REGISTRY/$image:$SHA" >/dev/null 2>&1; then
    die "$REGISTRY/$image:$SHA already exists; this commit is already released"
  fi
done

say "Logging in to ghcr.io"
gh auth token | docker login ghcr.io -u "$(gh api user -q .login)" --password-stdin >/dev/null \
  || die "ghcr.io login failed (gh auth refresh -s write:packages)"

# Build from a clean checkout of the commit, never the working tree.
SRC="$(mktemp -d)"
trap 'git -C "$ROOT" worktree remove --force "$SRC" >/dev/null 2>&1 || true' EXIT
git -C "$ROOT" worktree add -q --detach "$SRC" "$SHA"

tags_for() {  # tags_for <image>: :<sha>, plus :main when this is main's head
  local t="$REGISTRY/$1:$SHA"
  [[ "$SHA" == "$MAIN_SHA" ]] && t="$t,$REGISTRY/$1:main"
  echo "$t"
}

build() {  # build <image> <context> <dockerfile>
  local tags args=()
  tags="$(tags_for "$1")"
  IFS=, read -ra list <<<"$tags"
  for t in "${list[@]}"; do args+=(--tag "$t"); done
  say "Building $1 ($PLATFORM) -> $tags"
  docker buildx build --platform "$PLATFORM" --file "$SRC/$3" "${args[@]}" --push "$SRC/$2"
}

say "Releasing $SHA$([[ "$SHA" == "$MAIN_SHA" ]] && echo ' (origin/main head)')"
build backend . backend/Dockerfile.production
build frontend frontend frontend/Dockerfile.production
build pentest . backend/Dockerfile.pentest

say "Done"
for image in backend frontend pentest; do echo "  $REGISTRY/$image:$SHA"; done
