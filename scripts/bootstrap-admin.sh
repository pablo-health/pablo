#!/usr/bin/env bash
# Bootstrap the first platform admin user on a fresh Pablo deployment.
#
# This script:
# 1. Adds the admin email to the Firestore allowlist
# 2. Creates a project-level user in Identity Platform (no tenant)
# 3. Marks the user as is_admin in Firestore
# 4. Temporarily disables multi-tenancy on the frontend so the admin can sign in
# 5. Waits for confirmation, then re-enables multi-tenancy
#
# Usage:
#   ./scripts/bootstrap-admin.sh <email> [project-id]
#
# Examples:
#   ./scripts/bootstrap-admin.sh admin@example.com pablohealth-prod
#   ./scripts/bootstrap-admin.sh admin@example.com  # uses current gcloud project

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

ADMIN_EMAIL="${1:-}"
PROJECT_ID="${2:-$(gcloud config get-value project 2>/dev/null)}"
REGION="us-central1"

if [ -z "$ADMIN_EMAIL" ]; then
  echo -e "${RED}Usage: $0 <admin-email> [project-id]${NC}"
  exit 1
fi

if [ -z "$PROJECT_ID" ]; then
  echo -e "${RED}No project specified and no default gcloud project set.${NC}"
  exit 1
fi

ADMIN_EMAIL_LOWER=$(echo "$ADMIN_EMAIL" | tr '[:upper:]' '[:lower:]')
TOKEN=$(gcloud auth print-access-token)

echo ""
echo -e "${YELLOW}Bootstrapping admin user for ${PROJECT_ID}${NC}"
echo -e "  Email: ${ADMIN_EMAIL_LOWER}"
echo ""

# 1. Add to allowlist
echo -e "Adding to allowlist..."
curl -sf -X PATCH \
  "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/allowed_emails/${ADMIN_EMAIL_LOWER}?updateMask.fieldPaths=email&updateMask.fieldPaths=added_by&updateMask.fieldPaths=added_at" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  -d "{
    \"fields\": {
        \"email\": {\"stringValue\": \"${ADMIN_EMAIL_LOWER}\"},
        \"added_by\": {\"stringValue\": \"bootstrap-admin\"},
        \"added_at\": {\"stringValue\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}
    }
  }" > /dev/null
echo -e "${GREEN}  ✓ Allowlist updated${NC}"

# 2. Create project-level user in Identity Platform
echo -e "Creating Identity Platform user..."
RESULT=$(curl -sf \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  -X POST "https://identitytoolkit.googleapis.com/v1/projects/${PROJECT_ID}/accounts" \
  -d "{
    \"email\": \"${ADMIN_EMAIL_LOWER}\",
    \"emailVerified\": true
  }" 2>&1) || true

ADMIN_UID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('localId',''))" 2>/dev/null || echo "")

if [ -z "$ADMIN_UID" ]; then
  echo -e "${YELLOW}  ⚠ Could not create user (may already exist). Trying to look up...${NC}"
  # Look up existing user by email
  LOOKUP=$(curl -sf \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -X POST "https://identitytoolkit.googleapis.com/v1/projects/${PROJECT_ID}/accounts:lookup" \
    -d "{\"email\": [\"${ADMIN_EMAIL_LOWER}\"]}" 2>&1) || true
  ADMIN_UID=$(echo "$LOOKUP" | python3 -c "import sys,json; print(json.load(sys.stdin)['users'][0]['localId'])" 2>/dev/null || echo "")
fi

if [ -z "$ADMIN_UID" ]; then
  echo -e "${RED}  ✗ Could not create or find user. Check Identity Platform manually.${NC}"
  exit 1
fi

echo -e "${GREEN}  ✓ User UID: ${ADMIN_UID}${NC}"

# 3. Mark as admin in Firestore
echo -e "Setting admin flag in Firestore..."
curl -sf -X PATCH \
  "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/users/${ADMIN_UID}?updateMask.fieldPaths=id&updateMask.fieldPaths=email&updateMask.fieldPaths=is_admin&updateMask.fieldPaths=status&updateMask.fieldPaths=name&updateMask.fieldPaths=created_at" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "X-Goog-User-Project: ${PROJECT_ID}" \
  -d "{
    \"fields\": {
        \"id\": {\"stringValue\": \"${ADMIN_UID}\"},
        \"email\": {\"stringValue\": \"${ADMIN_EMAIL_LOWER}\"},
        \"is_admin\": {\"booleanValue\": true},
        \"status\": {\"stringValue\": \"approved\"},
        \"name\": {\"stringValue\": \"Platform Admin\"},
        \"created_at\": {\"stringValue\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"}
    }
  }" > /dev/null
echo -e "${GREEN}  ✓ Admin flag set${NC}"

# 4. Temporarily disable multi-tenancy for first sign-in
echo ""
echo -e "${YELLOW}Temporarily disabling multi-tenancy so you can sign in...${NC}"
gcloud run services update pablo-frontend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --update-env-vars="MULTI_TENANCY_ENABLED=false" \
  --quiet 2>/dev/null
echo -e "${GREEN}  ✓ Multi-tenancy disabled${NC}"

echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  Admin user ready! Sign in now at your frontend URL.         ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════════╝${NC}"
echo ""
read -p "Press Enter after you've signed in successfully..."

# 5. Re-enable multi-tenancy
echo ""
echo -e "${YELLOW}Re-enabling multi-tenancy...${NC}"
gcloud run services update pablo-frontend \
  --project="$PROJECT_ID" \
  --region="$REGION" \
  --update-env-vars="MULTI_TENANCY_ENABLED=true" \
  --quiet 2>/dev/null
echo -e "${GREEN}  ✓ Multi-tenancy re-enabled${NC}"

echo ""
echo -e "${GREEN}✓ Bootstrap complete!${NC}"
echo "  Admin: ${ADMIN_EMAIL_LOWER} (${ADMIN_UID})"
echo "  Project: ${PROJECT_ID}"
echo ""
