#!/bin/bash
#
# Redeploy Pablo
# Rebuilds container(s) and redeploys to Cloud Run, preserving all environment variables
#
# Usage:
#   ./redeploy.sh          # Redeploy both backend and frontend
#   ./redeploy.sh backend  # Redeploy backend only
#   ./redeploy.sh frontend # Redeploy frontend only
#   ./redeploy.sh base     # Rebuild backend base image (when deps change)
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Configuration
REGION="us-central1"
REPO_NAME="therapy"

# Get project ID
PROJECT_ID=$(gcloud config get-value project 2>/dev/null)
if [ -z "$PROJECT_ID" ]; then
    echo -e "${RED}Error: No GCP project configured. Run 'gcloud config set project PROJECT_ID'${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║          Pablo - Redeploy                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "Project: ${GREEN}${PROJECT_ID}${NC}"
echo -e "Region:  ${GREEN}${REGION}${NC}"
echo ""

# Determine what to deploy
TARGET=${1:-both}

deploy_base() {
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Building backend base image (Python deps)...${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    DATE_TAG=$(date +%Y%m%d)
    gcloud builds submit . \
        --config=./backend/cloudbuild-base.yaml \
        --substitutions="_DATE_TAG=${DATE_TAG}" \
        --timeout=30m \
        --quiet

    echo ""
    echo -e "${GREEN}✓ Backend base image built${NC}"
    echo -e "  Tags: backend-base:latest, backend-base:v${DATE_TAG}"
    echo ""
}

deploy_backend() {
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Building backend image...${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    gcloud builds submit . \
        --config=./backend/cloudbuild.yaml \
        --timeout=15m \
        --quiet

    echo ""
    echo -e "${GREEN}✓ Backend image built${NC}"
    echo ""

    echo -e "${YELLOW}Deploying backend (preserving environment)...${NC}"
    echo ""

    # Just update the image - Cloud Run preserves all other settings
    gcloud run services update therapy-backend \
        --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend:latest" \
        --region="$REGION" \
        --quiet

    # Ensure traffic routes to the new revision
    gcloud run services update-traffic therapy-backend \
        --to-latest \
        --region="$REGION" \
        --quiet

    BACKEND_URL=$(gcloud run services describe therapy-backend \
        --region="$REGION" \
        --format="value(status.url)")

    echo ""
    echo -e "${GREEN}✓ Backend deployed: ${BACKEND_URL}${NC}"
}

deploy_frontend() {
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}Building frontend image...${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""

    gcloud builds submit ./frontend \
        --config=./frontend/cloudbuild.yaml \
        --timeout=20m \
        --quiet

    echo ""
    echo -e "${GREEN}✓ Frontend image built${NC}"
    echo ""

    echo -e "${YELLOW}Deploying frontend (preserving environment)...${NC}"
    echo ""

    # Just update the image - Cloud Run preserves all other settings
    gcloud run services update therapy-frontend \
        --image="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/frontend:latest" \
        --region="$REGION" \
        --quiet

    # Ensure traffic routes to the new revision
    gcloud run services update-traffic therapy-frontend \
        --to-latest \
        --region="$REGION" \
        --quiet

    FRONTEND_URL=$(gcloud run services describe therapy-frontend \
        --region="$REGION" \
        --format="value(status.url)")

    echo ""
    echo -e "${GREEN}✓ Frontend deployed: ${FRONTEND_URL}${NC}"
}

case "$TARGET" in
    base)
        deploy_base
        ;;
    backend)
        deploy_backend
        ;;
    frontend)
        deploy_frontend
        ;;
    both|"")
        deploy_backend
        echo ""
        deploy_frontend
        ;;
    *)
        echo -e "${RED}Unknown target: $TARGET${NC}"
        echo ""
        echo "Usage: $0 [base|backend|frontend|both]"
        exit 1
        ;;
esac

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✓ Redeploy complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
