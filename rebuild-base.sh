#!/bin/bash
#
# Rebuild the backend base image (Python deps + spaCy model)
# Run this when pyproject.toml or poetry.lock changes.
#
# Usage:
#   ./rebuild-base.sh
#

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

DATE_TAG=$(date +%Y%m%d)

echo ""
echo "Building backend base image..."
echo "  Tags: backend-base:latest, backend-base:v${DATE_TAG}"
echo ""

gcloud builds submit . \
    --config=./backend/cloudbuild-base.yaml \
    --substitutions="_DATE_TAG=${DATE_TAG}" \
    --timeout=30m \
    --quiet

echo ""
echo "Base image built and pushed:"
echo "  backend-base:latest"
echo "  backend-base:v${DATE_TAG}"
echo ""
