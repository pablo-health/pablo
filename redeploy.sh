#!/bin/bash
#
# Redeploy Pablo
# Pulls the latest published images from ghcr.io into your Artifact Registry
# and rolls Cloud Run to the new revision. Environment variables and secrets
# on the Cloud Run service are preserved.
#
# Usage:
#   ./redeploy.sh                 # Redeploy both backend and frontend to :latest
#   ./redeploy.sh backend         # Redeploy backend only
#   ./redeploy.sh frontend        # Redeploy frontend only
#   PABLO_VERSION=v0.2.0 ./redeploy.sh  # Pin to a specific release tag
#

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

REGION="us-central1"
REPO_NAME="pablo"
PABLO_VERSION="${PABLO_VERSION:-latest}"

PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}Error: No GCP project configured. Run 'gcloud config set project PROJECT_ID'${NC}"
    exit 1
fi

if ! command -v docker &>/dev/null; then
    echo -e "${RED}Error: docker is required to mirror images.${NC}"
    echo "Cloud Shell includes it by default. Locally, install Docker Desktop:"
    echo "  https://docs.docker.com/get-docker/"
    exit 1
fi

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║          Pablo - Redeploy                                      ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Project: ${GREEN}${PROJECT_ID}${NC}"
echo -e "Region:  ${GREEN}${REGION}${NC}"
echo -e "Version: ${GREEN}${PABLO_VERSION}${NC}"
echo ""

TARGET=${1:-both}

mirror_and_deploy() {
    local service="$1"          # backend | frontend
    local cloud_run_name="pablo-${service}"
    local source="ghcr.io/pablo-health/${service}:${PABLO_VERSION}"
    local dest="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/${service}:${PABLO_VERSION}"

    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Pulling ${service} image from ghcr.io...${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    docker pull --platform linux/amd64 "$source"
    docker tag "$source" "$dest"
    docker push "$dest"
    echo -e "${GREEN}✓ ${service} image mirrored to Artifact Registry${NC}"
    echo ""

    echo -e "${YELLOW}Rolling Cloud Run to new revision...${NC}"
    gcloud run services update "$cloud_run_name" \
        --image="$dest" \
        --region="$REGION" \
        --quiet

    gcloud run services update-traffic "$cloud_run_name" \
        --to-latest \
        --region="$REGION" \
        --quiet

    local url
    url=$(gcloud run services describe "$cloud_run_name" \
        --region="$REGION" \
        --format="value(status.url)")

    echo ""
    echo -e "${GREEN}✓ ${service} deployed: ${url}${NC}"
}

case "$TARGET" in
    backend)
        mirror_and_deploy backend
        ;;
    frontend)
        mirror_and_deploy frontend
        ;;
    both|"")
        mirror_and_deploy backend
        echo ""
        mirror_and_deploy frontend
        ;;
    *)
        echo -e "${RED}Unknown target: $TARGET${NC}"
        echo ""
        echo "Usage: $0 [backend|frontend|both]"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Redeploy complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
