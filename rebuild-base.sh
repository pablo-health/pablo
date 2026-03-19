#!/bin/bash
#
# Rebuild the backend base image (Python dependencies)
# Run this when pyproject.toml or poetry.lock changes.
#
# Usage:
#   ./rebuild-base.sh
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

DEPS_HASH=$(cat pyproject.toml poetry.lock | shasum -a 256 | cut -c1-12)

echo ""
echo "Building backend base image..."
echo "  Tags: backend-base:latest, backend-base:deps-${DEPS_HASH}"
echo ""

gcloud builds submit . \
    --config=./backend/cloudbuild-base.yaml \
    --substitutions="_DATE_TAG=deps-${DEPS_HASH}" \
    --timeout=30m \
    --quiet

echo ""
echo "Base image built and pushed:"
echo "  backend-base:latest"
echo "  backend-base:deps-${DEPS_HASH}"
echo ""
