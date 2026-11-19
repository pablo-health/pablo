#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# The instance's startup script, attached by install.sh. It is the one file a
# VM never updates, so it does as little as it can: config from metadata, the
# deploy files for the release this VM runs, then that release's bootstrap.sh.
# Every boot step lives there, and moves with pablo-update.

# -E: the ERR trap below also fires inside fetch_release.
set -Eeuo pipefail

readonly INSTANCE="http://metadata.google.internal/computeMetadata/v1/instance"

# The guest attribute bootstrap.sh reports on, so install.sh stops waiting
# when the deploy files never arrive.
STEP="reading the configuration"
trap 'curl -fsS -X PUT -H "Metadata-Flavor: Google" --data "failed at: ${STEP} ${RUN_ID:-}" \
    "${INSTANCE}/guest-attributes/pablo/state" >/dev/null || true' ERR

mkdir -p /etc/pablo /opt/pablo/deploy
curl -fsS -H "Metadata-Flavor: Google" "${INSTANCE}/attributes/pablo-config" > /etc/pablo/config.env
chmod 0644 /etc/pablo/config.env
# shellcheck source=/dev/null
source /etc/pablo/config.env
# Settings made on this VM with pablo-config; read after config.env so they win.
# shellcheck source=/dev/null
[ ! -f /etc/pablo/local.env ] || source /etc/pablo/local.env

# The release this VM runs, once pablo-update has recorded one.
ref="$(cat /etc/pablo/ref 2>/dev/null || echo "${DEPLOY_REF:-$PABLO_VERSION}")"
release_dir="/opt/pablo/deploy/${ref//\//_}"

STEP="installing git"
if ! command -v git >/dev/null; then
    apt-get update -q
    DEBIAN_FRONTEND=noninteractive apt-get install -yq git
fi

# Fetch the deploy files at REF into DIR, sparse: deploy/vm, and backend/bin
# for allow_email.py. REF is a branch, a tag or a full commit SHA; fetch takes
# all three, where clone --branch takes only a name. A tag never moves, so a
# release fetched at one is not fetched again; a branch or a commit is. The
# directory starts again from nothing until a fetch has checked something out,
# so an interrupted first fetch heals on the next run.
# boot-stub.sh and pablo-update carry this function byte for byte (the stub
# cannot source a file it has not fetched yet); test_deploy_fetch_ref.py runs
# both copies and checks they match.
fetch_release() {
    local url="$1" ref="$2" dir="$3"
    if ! { [ -d "${dir}/.git" ] && git -C "$dir" rev-parse -q --verify HEAD >/dev/null 2>&1; }; then
        rm -rf "${dir:?}"
        git init -q "$dir"
        git -C "$dir" remote add origin "$url"
    elif [[ "$ref" == v* ]]; then
        git -C "$dir" sparse-checkout set deploy/vm backend/bin
        return 0
    fi
    git -C "$dir" sparse-checkout set deploy/vm backend/bin
    git -C "$dir" fetch -q --depth 1 --filter=blob:none origin "$ref"
    git -C "$dir" checkout -q --force --detach FETCH_HEAD
}

# The same layout and fetch as pablo-update.
STEP="fetching the deploy files at ${ref}"
fetch_release "$REPO_URL" "$ref" "$release_dir"

exec bash "${release_dir}/deploy/vm/bootstrap.sh"
