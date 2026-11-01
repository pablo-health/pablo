#!/usr/bin/env bash
# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.
#
# The instance's startup script, attached by install.sh. It is the one file a
# VM never updates, so it does as little as it can: config from metadata, the
# deploy files for the release this VM runs, then that release's bootstrap.sh.
# Every boot step lives there, and moves with pablo-update.

set -euo pipefail

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

# The same layout and fetch as pablo-update: a tag never moves, a branch does.
STEP="fetching the deploy files at ${ref}"
if [ ! -d "${release_dir}/.git" ]; then
    rm -rf "$release_dir"
    git clone -q --depth 1 --branch "$ref" --filter=blob:none --sparse "$REPO_URL" "$release_dir"
elif [[ "$ref" != v* ]]; then
    git -C "$release_dir" fetch -q --depth 1 origin "$ref"
    git -C "$release_dir" reset -q --hard FETCH_HEAD
fi
git -C "$release_dir" sparse-checkout set deploy/vm backend/bin

exec bash "${release_dir}/deploy/vm/bootstrap.sh"
