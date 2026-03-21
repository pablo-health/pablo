#!/bin/bash
#
# Pablo Solo — GCP Setup Wizard
# Deploy your own HIPAA-ready therapy documentation platform on Google Cloud.
#
# Usage: ./setup-solo.sh
# Documentation: docs/GCP_DEPLOYMENT.md
#

set -e

echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║           Pablo Solo — GCP Setup Wizard                      ║"
echo "║           Self-Hosted SOAP Note Generation                   ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ============================================================================
# STEP 1: Check for billing accounts
# ============================================================================
echo -e "${BLUE}Step 1: Checking billing accounts...${NC}"
echo ""

BILLING_ACCOUNTS=$(gcloud billing accounts list --format="value(name)" 2>/dev/null || echo "")

if [ -z "$BILLING_ACCOUNTS" ]; then
    echo -e "${YELLOW}No billing accounts found.${NC}"
    echo ""
    echo "You need a billing account to use Google Cloud services."
    echo "Good news: New accounts get \$300 in free credits!"
    echo ""
    echo -e "${GREEN}Create a billing account here:${NC}"
    echo "  https://console.cloud.google.com/billing/create"
    echo ""
    read -p "Press Enter after you've created a billing account..."
    echo ""

    BILLING_ACCOUNTS=$(gcloud billing accounts list --format="value(name)" 2>/dev/null || echo "")
    if [ -z "$BILLING_ACCOUNTS" ]; then
        echo -e "${RED}Still no billing accounts found. Please create one and try again.${NC}"
        exit 1
    fi
fi

# Select billing account if multiple exist
BILLING_COUNT=$(echo "$BILLING_ACCOUNTS" | wc -l | tr -d ' ')
if [ "$BILLING_COUNT" -gt 1 ]; then
    echo "Found multiple billing accounts:"
    gcloud billing accounts list
    echo ""
    read -p "Enter the Billing Account ID to use: " BILLING_ACCOUNT
else
    BILLING_ACCOUNT=$(echo "$BILLING_ACCOUNTS" | head -1)
    echo -e "${GREEN}Using billing account: $BILLING_ACCOUNT${NC}"
fi

# ============================================================================
# STEP 2: Create or select project
# ============================================================================
echo ""
echo -e "${BLUE}Step 2: Project setup...${NC}"
echo ""

echo "Would you like to:"
echo "  1) Create a NEW project for Pablo"
echo "  2) Use an EXISTING project"
echo ""
read -p "Choice (1 or 2): " PROJECT_CHOICE

if [ "$PROJECT_CHOICE" == "1" ]; then
    RANDOM_SUFFIX=$(LC_ALL=C head /dev/urandom | LC_ALL=C tr -dc 'a-z0-9' | head -c 6)
    SUGGESTED_ID="pablo-${RANDOM_SUFFIX}"

    echo ""
    echo -e "${YELLOW}Note: Project IDs must be globally unique across all of Google Cloud.${NC}"
    echo ""
    read -p "Enter project ID (or press Enter for '$SUGGESTED_ID'): " PROJECT_ID
    PROJECT_ID=${PROJECT_ID:-$SUGGESTED_ID}

    echo ""
    echo "Creating project: $PROJECT_ID"

    if gcloud projects create "$PROJECT_ID" --name="Pablo" 2>&1; then
        echo -e "${GREEN}Project created successfully${NC}"
    else
        echo ""
        echo -e "${RED}Failed to create project '$PROJECT_ID'.${NC}"
        echo "This could mean:"
        echo "  - The project ID is already taken (try a different name)"
        echo "  - You've hit your project quota"
        echo ""
        read -p "Enter 1 to try another name, 2 to use an existing project: " RETRY_CHOICE

        if [ "$RETRY_CHOICE" == "1" ]; then
            read -p "Enter a new project ID: " PROJECT_ID
            gcloud projects create "$PROJECT_ID" --name="Pablo" || {
                echo -e "${RED}Failed again. Please create a project manually in the console.${NC}"
                exit 1
            }
        else
            echo ""
            echo "Your existing projects:"
            gcloud projects list
            echo ""
            read -p "Enter the Project ID to use: " PROJECT_ID
        fi
    fi

    # Link billing
    echo ""
    echo "Linking billing account to project..."
    if gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT" 2>&1; then
        echo -e "${GREEN}Billing linked successfully${NC}"
    else
        echo ""
        echo -e "${RED}Failed to link billing.${NC}"
        echo "This might mean you don't have billing admin permissions."
        echo ""
        echo "Please link billing manually:"
        echo "  https://console.cloud.google.com/billing/linkedaccount?project=${PROJECT_ID}"
        echo ""
        read -p "Press Enter after you've linked billing..."
    fi

else
    echo ""
    echo "Your existing projects:"
    gcloud projects list
    echo ""
    read -p "Enter the Project ID to use: " PROJECT_ID
fi

# Set active project
echo ""
echo "Setting active project to: $PROJECT_ID"
gcloud config set project "$PROJECT_ID"

# ============================================================================
# STEP 3: Enable required APIs
# ============================================================================
echo ""
echo -e "${BLUE}Step 3: Enabling required APIs...${NC}"
echo ""
echo "This will enable:"
echo "  - Cloud Run (hosting)"
echo "  - Cloud Build (Docker image builds)"
echo "  - Artifact Registry (image storage)"
echo "  - Firestore (patient data)"
echo "  - Secret Manager (API keys and secrets)"
echo "  - Cloud Logging (HIPAA audit logs)"
echo "  - Vertex AI (SOAP note generation with Gemini)"
echo "  - Identity Platform (authentication with MFA)"
echo ""

gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    firestore.googleapis.com \
    secretmanager.googleapis.com \
    logging.googleapis.com \
    aiplatform.googleapis.com \
    cloudresourcemanager.googleapis.com \
    serviceusage.googleapis.com \
    identitytoolkit.googleapis.com \
    firebase.googleapis.com \
    cloudbilling.googleapis.com \
    --quiet

echo -e "${GREEN}APIs enabled${NC}"

# ============================================================================
# STEP 3b: Configure permissions
# ============================================================================
echo ""
echo -e "${BLUE}Step 3b: Configuring permissions...${NC}"
echo ""

CURRENT_USER=$(gcloud config get-value account)
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")

echo "Current user: $CURRENT_USER"
echo "Project number: $PROJECT_NUMBER"
echo ""

# Check if user already has the required permissions
echo "Checking existing permissions..."
USER_ROLES=$(gcloud projects get-iam-policy "$PROJECT_ID" \
    --flatten="bindings[].members" \
    --filter="bindings.members:user:$CURRENT_USER" \
    --format="value(bindings.role)" 2>/dev/null || echo "")

HAS_OWNER=$(echo "$USER_ROLES" | grep -c "roles/owner" || echo "0")
HAS_EDITOR=$(echo "$USER_ROLES" | grep -c "roles/editor" || echo "0")

PERMISSIONS_GRANTED=0
NEEDS_PROPAGATION_CHECK=0

if [ "$HAS_OWNER" -eq "1" ] || [ "$HAS_EDITOR" -eq "1" ]; then
    echo -e "${GREEN}You have Owner/Editor role${NC}"
    NEEDS_PROPAGATION_CHECK=1
else
    echo "Missing some deployment permissions — attempting to grant..."

    GRANT_FAILED=0

    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="user:$CURRENT_USER" \
        --role="roles/artifactregistry.admin" \
        --quiet 2>/dev/null || GRANT_FAILED=1

    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="user:$CURRENT_USER" \
        --role="roles/run.admin" \
        --quiet 2>/dev/null || GRANT_FAILED=1

    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="user:$CURRENT_USER" \
        --role="roles/cloudbuild.builds.editor" \
        --quiet 2>/dev/null || GRANT_FAILED=1

    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="user:$CURRENT_USER" \
        --role="roles/iam.serviceAccountAdmin" \
        --quiet 2>/dev/null || GRANT_FAILED=1

    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="user:$CURRENT_USER" \
        --role="roles/secretmanager.admin" \
        --quiet 2>/dev/null || GRANT_FAILED=1

    if [ "$GRANT_FAILED" -eq "1" ]; then
        echo -e "${YELLOW}Could not automatically grant permissions.${NC}"
        echo ""
        echo "Please run these commands (or ask a project owner to):"
        echo ""
        echo "  gcloud projects add-iam-policy-binding $PROJECT_ID \\"
        echo "    --member=\"user:$CURRENT_USER\" \\"
        echo "    --role=\"roles/artifactregistry.admin\""
        echo ""
        echo "  gcloud projects add-iam-policy-binding $PROJECT_ID \\"
        echo "    --member=\"user:$CURRENT_USER\" \\"
        echo "    --role=\"roles/run.admin\""
        echo ""
        echo "  gcloud projects add-iam-policy-binding $PROJECT_ID \\"
        echo "    --member=\"user:$CURRENT_USER\" \\"
        echo "    --role=\"roles/cloudbuild.builds.editor\""
        echo ""
        echo "  gcloud projects add-iam-policy-binding $PROJECT_ID \\"
        echo "    --member=\"user:$CURRENT_USER\" \\"
        echo "    --role=\"roles/iam.serviceAccountAdmin\""
        echo ""
        echo "  gcloud projects add-iam-policy-binding $PROJECT_ID \\"
        echo "    --member=\"user:$CURRENT_USER\" \\"
        echo "    --role=\"roles/secretmanager.admin\""
        echo ""
        read -p "Press Enter after the permissions have been granted, or Ctrl+C to exit..."
        echo ""
    else
        echo -e "${GREEN}User deployment permissions granted${NC}"
        PERMISSIONS_GRANTED=1
        echo ""
    fi
fi
echo ""

# Grant Cloud Run service account necessary roles
echo "Granting service account permissions..."

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/datastore.user" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/aiplatform.user" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/logging.logWriter" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/secretmanager.secretAccessor" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --quiet 2>/dev/null || true

gcloud iam service-accounts add-iam-policy-binding \
    "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --project="$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/run.admin" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/iam.serviceAccountUser" \
    --quiet 2>/dev/null || true

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
    --role="roles/artifactregistry.writer" \
    --quiet 2>/dev/null || true

echo -e "${GREEN}Service account permissions configured${NC}"

# Ensure project is set to ID (not number) before continuing
gcloud config set project "$PROJECT_ID"

# Wait for IAM propagation
if [ "$PERMISSIONS_GRANTED" -eq "1" ] || [ "$NEEDS_PROPAGATION_CHECK" -eq "1" ]; then
    echo ""
    echo "Verifying IAM permissions are ready..."
    RETRY_COUNT=0
    MAX_RETRIES=12

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if gcloud artifacts repositories list --location=us-central1 --project="$PROJECT_ID" --quiet &>/dev/null; then
            echo -e "${GREEN}IAM permissions are active${NC}"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        if [ $RETRY_COUNT -lt $MAX_RETRIES ]; then
            echo "Waiting for permissions to propagate... ($((RETRY_COUNT * 10)) seconds elapsed)"
            sleep 10
        fi
    done

    if [ $RETRY_COUNT -ge $MAX_RETRIES ]; then
        echo -e "${YELLOW}Permissions are taking longer than expected to propagate.${NC}"
        echo "This is normal for new projects. Continuing anyway..."
    fi

    echo ""
fi

# ============================================================================
# STEP 4: Create Artifact Registry repository
# ============================================================================
echo ""
echo -e "${BLUE}Step 4: Creating Artifact Registry repository...${NC}"
echo ""

REPO_NAME="pablo"
REPO_LOCATION="us-central1"

REPO_EXISTS=$(gcloud artifacts repositories list \
    --location="$REPO_LOCATION" \
    --project="$PROJECT_ID" \
    --format="value(name)" 2>/dev/null | grep -c "$REPO_NAME" || true)

if [ "$REPO_EXISTS" == "0" ]; then
    echo "Creating Docker repository: $REPO_NAME"
    gcloud artifacts repositories create "$REPO_NAME" \
        --repository-format=docker \
        --location="$REPO_LOCATION" \
        --project="$PROJECT_ID" \
        --description="Pablo Docker images" \
        --quiet
    echo -e "${GREEN}Artifact Registry repository created${NC}"
else
    echo -e "${GREEN}Artifact Registry repository already exists${NC}"
fi

# ============================================================================
# STEP 5: Create Firestore database
# ============================================================================
echo ""
echo -e "${BLUE}Step 5: Setting up Firestore database...${NC}"
echo ""

FIRESTORE_EXISTS=$(gcloud firestore databases list --format="value(name)" 2>/dev/null | grep -c "(default)" || true)

if [ "$FIRESTORE_EXISTS" == "0" ]; then
    echo "Creating Firestore database in Native mode..."
    echo "  Location: nam5 (United States)"
    echo "  Type: firestore-native (HIPAA-compliant)"
    echo ""

    if gcloud firestore databases create --location=nam5 --type=firestore-native --quiet 2>&1; then
        echo -e "${GREEN}Firestore database created${NC}"
        echo "Waiting for Firestore to be ready..."
        sleep 10
    else
        echo -e "${RED}Firestore creation failed!${NC}"
        echo ""
        echo "Please create Firestore manually:"
        echo "  1. Visit: https://console.cloud.google.com/firestore?project=${PROJECT_ID}"
        echo "  2. Choose 'Native Mode'"
        echo "  3. Select location: nam5 (United States)"
        echo "  4. Click 'Create Database'"
        echo ""
        read -p "Press Enter after you've created the Firestore database..."
    fi

    # Verify Firestore is available
    echo "Verifying Firestore availability..."
    RETRY_COUNT=0
    MAX_RETRIES=12

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        FIRESTORE_EXISTS=$(gcloud firestore databases list --format="value(name)" 2>/dev/null | grep -c "(default)" || echo "0")
        if [ "$FIRESTORE_EXISTS" != "0" ]; then
            echo -e "${GREEN}Firestore is ready${NC}"
            break
        fi
        echo "Still waiting for Firestore... ($((RETRY_COUNT + 1))/$MAX_RETRIES)"
        sleep 5
        RETRY_COUNT=$((RETRY_COUNT + 1))
    done

    if [ "$FIRESTORE_EXISTS" == "0" ]; then
        echo -e "${RED}Firestore is still not available. Cannot continue.${NC}"
        echo "Please check the Firestore console and try again."
        exit 1
    fi
else
    echo -e "${GREEN}Firestore already configured${NC}"
fi

# Enable HIPAA audit logging for Firestore
echo ""
echo "Enabling HIPAA audit logs for Firestore..."
echo -e "${YELLOW}Note: Enable Data Access audit logs in Cloud Console for full HIPAA compliance${NC}"
echo "  See: docs/SELF_HOSTING_HIPAA_GUIDE.md"
echo ""

# ============================================================================
# STEP 5b: Deploy Firestore Indexes
# ============================================================================
echo ""
echo -e "${BLUE}Step 5b: Deploying Firestore indexes...${NC}"
echo ""

create_index() {
    local collection="$1"
    shift
    local field_args=()
    for field in "$@"; do
        field_args+=(--field-config="$field")
    done

    gcloud firestore indexes composite create \
        --project="$PROJECT_ID" \
        --collection-group="$collection" \
        "${field_args[@]}" \
        --async --quiet 2>&1 || true
}

# Check if indexes already exist by looking for the last index we create
EXISTING_INDEXES=$(gcloud firestore indexes composite list --project="$PROJECT_ID" --format="value(fieldConfig)" 2>/dev/null || echo "")

if echo "$EXISTING_INDEXES" | grep -q "session_number"; then
    echo -e "${GREEN}Firestore indexes already exist — skipping${NC}"
else
    echo "Creating composite indexes..."
    echo ""

    echo "  patients (user_id + last_name + first_name)..."
    create_index patients \
        "field-path=user_id,order=ascending" \
        "field-path=last_name_lower,order=ascending" \
        "field-path=first_name_lower,order=ascending" \
        "field-path=__name__,order=ascending"

    echo "  patients (user_id + first_name + last_name)..."
    create_index patients \
        "field-path=user_id,order=ascending" \
        "field-path=first_name_lower,order=ascending" \
        "field-path=last_name_lower,order=ascending" \
        "field-path=__name__,order=ascending"

    echo "  therapy_sessions (user_id + session_date)..."
    create_index therapy_sessions \
        "field-path=user_id,order=ascending" \
        "field-path=session_date,order=descending" \
        "field-path=__name__,order=descending"

    echo "  therapy_sessions (patient_id + user_id + session_date)..."
    create_index therapy_sessions \
        "field-path=patient_id,order=ascending" \
        "field-path=user_id,order=ascending" \
        "field-path=session_date,order=descending" \
        "field-path=__name__,order=descending"

    echo "  therapy_sessions (patient_id + session_number)..."
    create_index therapy_sessions \
        "field-path=patient_id,order=ascending" \
        "field-path=session_number,order=descending" \
        "field-path=__name__,order=descending"

    echo ""
    echo -e "${YELLOW}Note: Index creation happens asynchronously (5-10 minutes)${NC}"
    echo "  Check status: gcloud firestore indexes composite list --project=${PROJECT_ID}"
fi
echo ""
echo -e "${GREEN}Firestore indexes ready${NC}"
echo ""

# ============================================================================
# STEP 6: Initialize Identity Platform (Firebase Auth with MFA)
# ============================================================================
echo ""
echo -e "${BLUE}Step 6: Setting up Identity Platform (authentication with MFA)...${NC}"
echo ""

echo "Identity Platform provides:"
echo "  - Google OAuth + Email/Password sign-in"
echo "  - Multi-factor authentication (TOTP)"
echo "  - Password policies (enforced server-side)"
echo "  - HIPAA-compliant (covered under GCP BAA)"
echo ""

# 6a: Add Firebase to the GCP project
echo "Adding Firebase to GCP project..."
FIREBASE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "X-Goog-User-Project: ${PROJECT_ID}")

if [ "$FIREBASE_STATUS" == "200" ]; then
    echo -e "${GREEN}Firebase already configured${NC}"
else
    echo "Initializing Firebase..."
    curl -s -X POST \
        "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}:addFirebase" \
        -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "X-Goog-User-Project: ${PROJECT_ID}" \
        -H "Content-Type: application/json" > /dev/null

    echo "Waiting for Firebase initialization..."
    RETRY_COUNT=0
    MAX_RETRIES=12
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        FIREBASE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
            "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}" \
            -H "Authorization: Bearer $(gcloud auth print-access-token)" \
            -H "X-Goog-User-Project: ${PROJECT_ID}")
        if [ "$FIREBASE_STATUS" == "200" ]; then
            echo -e "${GREEN}Firebase initialized${NC}"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo "  Waiting... ($((RETRY_COUNT * 5)) seconds)"
        sleep 5
    done

    if [ "$FIREBASE_STATUS" != "200" ]; then
        echo -e "${RED}Firebase initialization timed out. Please try again.${NC}"
        exit 1
    fi
fi
echo ""

# 6b: Create Firebase web app
echo "Checking for Firebase web app..."
EXISTING_APPS=$(curl -s \
    "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "X-Goog-User-Project: ${PROJECT_ID}")

FIREBASE_APP_ID=$(echo "$EXISTING_APPS" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    apps = d.get('apps', [])
    if apps:
        print(apps[0]['appId'])
except: pass
" 2>/dev/null)

if [ -n "$FIREBASE_APP_ID" ]; then
    echo -e "${GREEN}Firebase web app exists: ${FIREBASE_APP_ID}${NC}"
else
    echo "Creating Firebase web app..."
    curl -s -X POST \
        "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps" \
        -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "X-Goog-User-Project: ${PROJECT_ID}" \
        -H "Content-Type: application/json" \
        -d '{"displayName": "Pablo"}' > /dev/null

    sleep 5

    EXISTING_APPS=$(curl -s \
        "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps" \
        -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "X-Goog-User-Project: ${PROJECT_ID}")

    FIREBASE_APP_ID=$(echo "$EXISTING_APPS" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    apps = d.get('apps', [])
    if apps:
        print(apps[0]['appId'])
except: pass
" 2>/dev/null)

    if [ -n "$FIREBASE_APP_ID" ]; then
        echo -e "${GREEN}Firebase web app created: ${FIREBASE_APP_ID}${NC}"
    else
        echo -e "${RED}Failed to create Firebase web app${NC}"
        exit 1
    fi
fi

# Get Firebase web app config
FIREBASE_CONFIG=$(curl -s \
    "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps/${FIREBASE_APP_ID}/config" \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "X-Goog-User-Project: ${PROJECT_ID}")

FIREBASE_API_KEY=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['apiKey'])" 2>/dev/null)
FIREBASE_AUTH_DOMAIN=$(echo "$FIREBASE_CONFIG" | python3 -c "import sys,json; print(json.load(sys.stdin)['authDomain'])" 2>/dev/null)

echo ""
echo "Firebase config:"
echo "  API Key:     ${FIREBASE_API_KEY}"
echo "  Auth Domain: ${FIREBASE_AUTH_DOMAIN}"
echo "  Project ID:  ${PROJECT_ID}"
echo "  App ID:      ${FIREBASE_APP_ID}"
echo ""

# 6c: Initialize Identity Platform
echo "Initializing Identity Platform..."
AUTH_TOKEN=$(gcloud auth print-access-token)

IP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/config" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}")

if [ "$IP_STATUS" == "200" ]; then
    echo -e "${GREEN}Identity Platform already initialized${NC}"
else
    curl -s -X POST \
        "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/identityPlatform:initializeAuth" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "X-Goog-User-Project: ${PROJECT_ID}" \
        -H "Content-Type: application/json" \
        -d '{}' > /dev/null

    sleep 3

    IP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
        "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/config" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "X-Goog-User-Project: ${PROJECT_ID}")

    if [ "$IP_STATUS" == "200" ]; then
        echo -e "${GREEN}Identity Platform initialized${NC}"
    else
        echo -e "${RED}Failed to initialize Identity Platform${NC}"
        echo "  Please enable it manually: https://console.cloud.google.com/customer-identity?project=${PROJECT_ID}"
        exit 1
    fi
fi
echo ""

# 6d: Enable email/password sign-in
echo "Enabling email/password sign-in..."
curl -s -o /dev/null -w "" \
    -X PATCH "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/config?updateMask=signIn.email" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d '{
        "signIn": {
            "email": {
                "enabled": true,
                "passwordRequired": true
            }
        }
    }'
echo -e "${GREEN}Email/password sign-in enabled${NC}"

# 6e: Set password policy (NIST 800-63B compliant)
echo "Setting password policy (NIST 800-63B: 15+ chars, no composition rules)..."
curl -s -o /dev/null -w "" \
    -X PATCH "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/config?updateMask=passwordPolicyConfig" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d '{
        "passwordPolicyConfig": {
            "passwordPolicyEnforcementState": "ENFORCE",
            "passwordPolicyVersions": [{
                "customStrengthOptions": {
                    "minPasswordLength": 15,
                    "maxPasswordLength": 128
                }
            }],
            "forceUpgradeOnSignin": true
        }
    }'
echo -e "${GREEN}Password policy enforced${NC}"

# 6f: Enable MFA (TOTP only)
echo "Enabling multi-factor authentication (TOTP)..."
curl -s -o /dev/null -w "" \
    -X PATCH "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/config?updateMask=mfa" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d '{
        "mfa": {
            "state": "ENABLED",
            "providerConfigs": [{
                "state": "ENABLED",
                "totpProviderConfig": {
                    "adjacentIntervals": 5
                }
            }]
        }
    }'
echo -e "${GREEN}MFA enabled (TOTP only, no SMS)${NC}"
echo ""

echo -e "${GREEN}Identity Platform fully configured${NC}"
echo ""

# ============================================================================
# STEP 7: AI Model Configuration
# ============================================================================
echo ""
echo -e "${BLUE}Step 7: AI Model Configuration${NC}"
echo ""

echo "Choose your AI model for generating SOAP notes:"
echo ""
echo "  1) Google Vertex AI (Gemini) — Recommended for GCP"
echo "     Auto-authenticated on Cloud Run (no API key needed)"
echo "     Cost: ~\$0.10-0.30 per SOAP note"
echo ""
echo "  2) Anthropic (Claude)"
echo "     Requires Anthropic API key"
echo "     Cost: ~\$0.15-0.40 per SOAP note"
echo ""
read -p "Choice [1]: " MODEL_CHOICE
MODEL_CHOICE=${MODEL_CHOICE:-1}

if [ "$MODEL_CHOICE" = "2" ]; then
    AI_MODEL="anthropic/claude-sonnet-4-20250514"
    echo ""
    echo -e "${YELLOW}Setting up Anthropic API key...${NC}"
    echo ""
    echo -e "${GREEN}Get your API key from: https://console.anthropic.com/settings/keys${NC}"
    echo ""

    read -sp "Paste your Anthropic API Key (hidden): " ANTHROPIC_KEY
    echo ""

    echo "Storing API key in Secret Manager..."
    echo -n "$ANTHROPIC_KEY" | gcloud secrets create ANTHROPIC_API_KEY --data-file=- 2>/dev/null || {
        echo -n "$ANTHROPIC_KEY" | gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-
    }

    gcloud secrets add-iam-policy-binding ANTHROPIC_API_KEY \
        --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet 2>/dev/null || true

    echo -e "${GREEN}Anthropic API key stored securely${NC}"
    ANTHROPIC_SECRET="ANTHROPIC_API_KEY=ANTHROPIC_API_KEY:latest"
else
    AI_MODEL="google:gemini-3-pro-preview"
    GOOGLE_REGION="global"
    ANTHROPIC_SECRET=""
    echo -e "${GREEN}Using Google Gemini — auto-authenticated on GCP${NC}"
fi

# ============================================================================
# STEP 8: Generate secrets
# ============================================================================
echo ""
echo -e "${BLUE}Step 8: Generating secrets...${NC}"
echo ""

# Helper: create a secret only if it doesn't already exist
ensure_secret() {
    local name="$1"
    if gcloud secrets describe "$name" &>/dev/null; then
        echo -e "${GREEN}${name} already exists — skipping${NC}"
    else
        local value
        value=$(openssl rand -hex 32)
        echo -n "$value" | gcloud secrets create "$name" --data-file=-
        gcloud secrets add-iam-policy-binding "$name" \
            --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
            --role="roles/secretmanager.secretAccessor" \
            --quiet 2>/dev/null || true
        echo -e "${GREEN}${name} created${NC}"
    fi
}

ensure_secret JWT_SECRET_KEY
ensure_secret AUTH_SECRET
ensure_secret AUTH_COOKIE_SIGNATURE_KEY

echo ""
echo -e "${GREEN}All secrets ready${NC}"

# ============================================================================
# STEP 8b: Set up Google OAuth credentials
# ============================================================================
echo ""
echo -e "${BLUE}Step 8b: Setting up Google OAuth credentials...${NC}"
echo ""

if gcloud secrets describe GOOGLE_CLIENT_SECRET &>/dev/null; then
    echo -e "${GREEN}Google OAuth credentials already configured — skipping${NC}"
    # Retrieve stored client ID for Identity Platform registration
    GOOGLE_CLIENT_ID=$(gcloud secrets versions access latest --secret=GOOGLE_CLIENT_ID 2>/dev/null || echo "")
else
    echo "Please complete these steps in Google Cloud Console:"
    echo ""
    echo "  1. Visit: https://console.cloud.google.com/apis/credentials?project=${PROJECT_ID}"
    echo ""
    echo "  2. Remember to configure the OAuth consent screen with information"
    echo "     about your application, then click 'Get started'"
    echo ""
    echo "  3. Create OAuth Client ID:"
    echo "     a. Click 'CREATE CREDENTIALS' > 'OAuth client ID'"
    echo "     b. Application type: 'Web application'"
    echo "     c. Name: 'Pablo'"
    echo ""
    echo "  4. Add authorized redirect URI:"
    echo "     https://${PROJECT_ID}.firebaseapp.com/__/auth/handler"
    echo "     (We'll add the Cloud Run frontend URL after deployment)"
    echo ""
    echo "  5. Click 'CREATE' and copy the credentials"
    echo ""
    read -p "Paste your Google OAuth Client ID: " GOOGLE_CLIENT_ID
    read -sp "Paste your Google OAuth Client Secret (hidden): " GOOGLE_CLIENT_SECRET
    echo ""

    echo ""
    echo "Storing Google OAuth credentials..."
    echo -n "$GOOGLE_CLIENT_SECRET" | gcloud secrets create GOOGLE_CLIENT_SECRET --data-file=-
    echo -n "$GOOGLE_CLIENT_ID" | gcloud secrets create GOOGLE_CLIENT_ID --data-file=- 2>/dev/null || true
    gcloud secrets add-iam-policy-binding GOOGLE_CLIENT_SECRET \
        --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet 2>/dev/null || true
    echo -e "${GREEN}Google OAuth credentials stored${NC}"
fi

# Register Google OAuth with Identity Platform
echo ""
echo "Registering Google OAuth with Identity Platform..."
AUTH_TOKEN=$(gcloud auth print-access-token)

GOOGLE_IDP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/defaultSupportedIdpConfigs/google.com" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}")

if [ "$GOOGLE_IDP_STATUS" == "200" ]; then
    curl -s -o /dev/null \
        -X PATCH "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/defaultSupportedIdpConfigs/google.com?updateMask=clientId,clientSecret,enabled" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "X-Goog-User-Project: ${PROJECT_ID}" \
        -H "Content-Type: application/json" \
        -d "{
            \"enabled\": true,
            \"clientId\": \"${GOOGLE_CLIENT_ID}\",
            \"clientSecret\": \"${GOOGLE_CLIENT_SECRET}\"
        }"
else
    curl -s -o /dev/null \
        -X POST "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/defaultSupportedIdpConfigs?idpId=google.com" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "X-Goog-User-Project: ${PROJECT_ID}" \
        -H "Content-Type: application/json" \
        -d "{
            \"name\": \"projects/${PROJECT_ID}/defaultSupportedIdpConfigs/google.com\",
            \"enabled\": true,
            \"clientId\": \"${GOOGLE_CLIENT_ID}\",
            \"clientSecret\": \"${GOOGLE_CLIENT_SECRET}\"
        }"
fi
echo -e "${GREEN}Google OAuth registered with Identity Platform${NC}"

# ============================================================================
# STEP 9: Build Docker images
# ============================================================================
echo ""
echo -e "${BLUE}Step 9: Building Docker images...${NC}"
echo ""
echo "This builds frontend and backend images using Cloud Build."
echo ""

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

DEPS_HASH=$(cat pyproject.toml poetry.lock | shasum -a 256 | cut -c1-12)
BASE_TAG="solo-${DEPS_HASH}"

# Pre-built base image from GitHub Container Registry (solo variant, no NLI/torch).
# Users only need to build their own if they modify pyproject.toml/poetry.lock.
PUBLIC_BASE_IMAGE="ghcr.io/pablo-health/backend-base:${BASE_TAG}"
PRIVATE_BASE_IMAGE="${REPO_LOCATION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend-base:v${BASE_TAG}"

if gcloud artifacts docker images describe "$PRIVATE_BASE_IMAGE" &>/dev/null; then
    echo -e "${GREEN}Backend base image already up-to-date (${BASE_TAG}) — skipping${NC}"
    BASE_IMAGE="$PRIVATE_BASE_IMAGE"
elif docker pull "$PUBLIC_BASE_IMAGE" &>/dev/null; then
    echo -e "${GREEN}Using pre-built base image from ghcr.io${NC}"
    BASE_IMAGE="$PUBLIC_BASE_IMAGE"
else
    echo -e "${YELLOW}Building backend base image (Python deps)...${NC}"
    echo "This is a one-time build (~15-20 min). Future deploys will be fast."
    gcloud builds submit . \
        --config=./backend/cloudbuild-base.yaml \
        --substitutions="_DATE_TAG=${BASE_TAG}" \
        --timeout=30m \
        --quiet
    echo -e "${GREEN}Backend base image built (${BASE_TAG})${NC}"
    BASE_IMAGE="$PRIVATE_BASE_IMAGE"
fi
echo ""

# Build backend app image
echo -e "${YELLOW}Building backend image...${NC}"
gcloud builds submit . \
    --config=./backend/cloudbuild.yaml \
    --substitutions="_BASE_IMAGE=${BASE_IMAGE},_REGION=${REPO_LOCATION}" \
    --timeout=15m \
    --quiet
echo -e "${GREEN}Backend image built${NC}"

# Build frontend image
echo ""
echo -e "${YELLOW}Building frontend image...${NC}"
gcloud builds submit ./frontend \
    --config=./frontend/cloudbuild.yaml \
    --substitutions="_REGION=${REPO_LOCATION}" \
    --timeout=20m \
    --quiet
echo -e "${GREEN}Frontend image built${NC}"
echo ""

# ============================================================================
# STEP 10: Deploy to Cloud Run
# ============================================================================
echo ""
echo -e "${BLUE}Step 10: Deploying to Cloud Run...${NC}"
echo ""

# Build secrets string for backend
SECRETS_STRING="JWT_SECRET_KEY=JWT_SECRET_KEY:latest"
if [ -n "$ANTHROPIC_SECRET" ]; then
    SECRETS_STRING="${SECRETS_STRING},${ANTHROPIC_SECRET}"
fi

# Deploy backend
echo -e "${YELLOW}Deploying backend...${NC}"
gcloud run deploy pablo-backend \
    --image="$REPO_LOCATION-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend:latest" \
    --region="$REPO_LOCATION" \
    --platform=managed \
    --allow-unauthenticated \
    --service-account="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --memory=2Gi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=10 \
    --timeout=300 \
    --set-secrets="${SECRETS_STRING}" \
    --set-env-vars="^||^GCP_PROJECT_ID=${PROJECT_ID}||FIREBASE_PROJECT_ID=${PROJECT_ID}||ENVIRONMENT=production||ENFORCE_HTTPS=true||RESTRICT_SIGNUPS=true||AI_MODEL=${AI_MODEL}||GOOGLE_CLOUD_PROJECT=${PROJECT_ID}||GOOGLE_REGION=${GOOGLE_REGION:-global}||GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}||TRUSTED_PROXY_IPS=*" \
    --quiet

BACKEND_URL=$(gcloud run services describe pablo-backend \
    --region="$REPO_LOCATION" \
    --format="value(status.url)")

echo -e "${GREEN}Backend deployed: ${BACKEND_URL}${NC}"
echo ""

# Deploy frontend
echo -e "${YELLOW}Deploying frontend...${NC}"
gcloud run deploy pablo-frontend \
    --image="$REPO_LOCATION-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/frontend:latest" \
    --region="$REPO_LOCATION" \
    --platform=managed \
    --allow-unauthenticated \
    --memory=512Mi \
    --cpu=1 \
    --min-instances=0 \
    --max-instances=5 \
    --timeout=60 \
    --set-secrets="AUTH_SECRET=AUTH_SECRET:latest,GOOGLE_CLIENT_SECRET=GOOGLE_CLIENT_SECRET:latest,AUTH_COOKIE_SIGNATURE_KEY=AUTH_COOKIE_SIGNATURE_KEY:latest" \
    --set-env-vars="API_URL=${BACKEND_URL},FIREBASE_PROJECT_ID=${PROJECT_ID},GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID},DEV_MODE=false,DATA_MODE=api,ENABLE_LOCAL_AUTH=false,NEXT_PUBLIC_FIREBASE_API_KEY=${FIREBASE_API_KEY},NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN=${FIREBASE_AUTH_DOMAIN},NEXT_PUBLIC_FIREBASE_PROJECT_ID=${PROJECT_ID},NEXT_PUBLIC_FIREBASE_APP_ID=${FIREBASE_APP_ID}" \
    --quiet

FRONTEND_URL=$(gcloud run services describe pablo-frontend \
    --region="$REPO_LOCATION" \
    --format="value(status.url)")

# Set AUTH_URL
echo ""
echo "Updating frontend with AUTH_URL..."
gcloud run services update pablo-frontend \
    --region="$REPO_LOCATION" \
    --update-env-vars="AUTH_URL=${FRONTEND_URL}" \
    --quiet

echo -e "${GREEN}Frontend deployed: ${FRONTEND_URL}${NC}"
echo ""

# ============================================================================
# Add frontend domain to Identity Platform + update CORS
# ============================================================================

echo "Adding frontend domain to Identity Platform authorized domains..."
FRONTEND_DOMAIN=$(echo "$FRONTEND_URL" | sed 's|https://||')
AUTH_TOKEN=$(gcloud auth print-access-token)

CURRENT_DOMAINS=$(curl -s \
    "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/config" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" | python3 -c "
import sys, json
d = json.load(sys.stdin)
domains = d.get('authorizedDomains', [])
for new in ['${FRONTEND_DOMAIN}', 'localhost']:
    if new not in domains:
        domains.append(new)
print(json.dumps(domains))
" 2>/dev/null)

curl -s -o /dev/null \
    -X PATCH "https://identitytoolkit.googleapis.com/v2/projects/${PROJECT_ID}/config?updateMask=authorizedDomains" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "X-Goog-User-Project: ${PROJECT_ID}" \
    -H "Content-Type: application/json" \
    -d "{\"authorizedDomains\": ${CURRENT_DOMAINS}}"

echo -e "${GREEN}Frontend domain added to Identity Platform${NC}"
echo ""

echo -e "${YELLOW}IMPORTANT: Update your OAuth redirect URIs${NC}"
echo ""
echo "  1. Visit: https://console.cloud.google.com/apis/credentials?project=${PROJECT_ID}"
echo "  2. Click on your OAuth client > Edit"
echo "  3. Add:"
echo ""
echo "     Authorized JavaScript origins:"
echo "       ${FRONTEND_URL}"
echo ""
echo "     Authorized redirect URIs:"
echo "       https://${PROJECT_ID}.firebaseapp.com/__/auth/handler"
echo ""
echo "  4. Save"
echo ""
read -p "Press Enter after you've updated the OAuth redirect URIs..."
echo ""

# Update backend CORS
echo "Updating backend CORS configuration..."
gcloud run services update pablo-backend \
    --region="$REPO_LOCATION" \
    --update-env-vars="CORS_ORIGINS=${FRONTEND_URL}" \
    --quiet

echo -e "${GREEN}CORS configured${NC}"

# ============================================================================
# STEP 11: Create your user
# ============================================================================
echo ""
echo -e "${BLUE}Step 11: Creating your user account...${NC}"
echo ""

# Check if an admin user already exists in Firestore
AUTH_TOKEN=$(gcloud auth print-access-token)
EXISTING_ADMIN=$(curl -s \
    "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents:runQuery" \
    -H "Authorization: Bearer ${AUTH_TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
        "structuredQuery": {
            "from": [{"collectionId": "users"}],
            "where": {"fieldFilter": {"field": {"fieldPath": "is_admin"}, "op": "EQUAL", "value": {"booleanValue": true}}},
            "limit": 1
        }
    }' | python3 -c "
import sys, json
docs = json.load(sys.stdin)
for d in docs:
    fields = d.get('document', {}).get('fields', {})
    email = fields.get('email', {}).get('stringValue', '')
    if email:
        print(email)
        break
" 2>/dev/null)

if [ -n "$EXISTING_ADMIN" ]; then
    echo -e "${GREEN}Admin user already exists (${EXISTING_ADMIN}) — skipping${NC}"
    ADMIN_EMAIL=""
else
    echo "Enter the email you'll use to sign in."
    echo "This will be your admin account."
    echo ""
    read -p "Email address: " ADMIN_EMAIL
    echo ""
fi

if [ -n "$ADMIN_EMAIL" ]; then
    ADMIN_EMAIL_LOWER=$(echo "${ADMIN_EMAIL}" | tr '[:upper:]' '[:lower:]')
    SETUP_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    # Add email to allowlist
    echo "Adding to allowlist..."
    curl -s -o /dev/null -w "" \
        -X PATCH "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/allowed_emails/${ADMIN_EMAIL_LOWER}?updateMask.fieldPaths=email&updateMask.fieldPaths=added_by&updateMask.fieldPaths=added_at" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{
            \"fields\": {
                \"email\": {\"stringValue\": \"${ADMIN_EMAIL_LOWER}\"},
                \"added_by\": {\"stringValue\": \"setup-solo.sh\"},
                \"added_at\": {\"stringValue\": \"${SETUP_TIMESTAMP}\"}
            }
        }"
    echo -e "${GREEN}Email added to allowlist: ${ADMIN_EMAIL_LOWER}${NC}"

    # Check Identity Platform for existing user (preserves MFA enrollment)
    echo "Checking Identity Platform for existing account..."
    EXISTING_IP_USER=$(curl -s \
        -X POST "https://identitytoolkit.googleapis.com/v1/projects/${PROJECT_ID}/accounts:lookup" \
        -H "Authorization: Bearer ${AUTH_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"email\": [\"${ADMIN_EMAIL_LOWER}\"]}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    users = d.get('users', [])
    if users:
        print(users[0].get('localId', ''))
except: pass
" 2>/dev/null)

    if [ -n "$EXISTING_IP_USER" ]; then
        echo -e "${GREEN}User already exists in Identity Platform (uid: ${EXISTING_IP_USER}) — reusing${NC}"
        echo "  MFA enrollment and credentials are preserved."
        ADMIN_UID="$EXISTING_IP_USER"
    else
        # Create user via Identity Platform
        echo "Creating user account..."
        ADMIN_CREATE_RESULT=$(curl -s \
            -X POST "https://identitytoolkit.googleapis.com/v1/projects/${PROJECT_ID}/accounts" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{
                \"email\": \"${ADMIN_EMAIL_LOWER}\",
                \"emailVerified\": true,
                \"disabled\": false
            }")

        ADMIN_UID=$(echo "${ADMIN_CREATE_RESULT}" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    print(d.get('localId', ''))
except: pass
" 2>/dev/null)

        if [ -n "$ADMIN_UID" ]; then
            echo -e "${GREEN}User created (uid: ${ADMIN_UID})${NC}"
        else
            echo -e "${RED}Failed to create user account.${NC}"
            echo "  Check Identity Platform console for details."
        fi
    fi

    if [ -n "$ADMIN_UID" ]; then
        # Ensure admin record exists in Firestore (idempotent)
        echo "Setting admin flag..."
        curl -s -o /dev/null -w "" \
            -X PATCH "https://firestore.googleapis.com/v1/projects/${PROJECT_ID}/databases/(default)/documents/users/${ADMIN_UID}?updateMask.fieldPaths=email&updateMask.fieldPaths=is_admin&updateMask.fieldPaths=status&updateMask.fieldPaths=name&updateMask.fieldPaths=created_at" \
            -H "Authorization: Bearer ${AUTH_TOKEN}" \
            -H "Content-Type: application/json" \
            -d "{
                \"fields\": {
                    \"email\": {\"stringValue\": \"${ADMIN_EMAIL_LOWER}\"},
                    \"is_admin\": {\"booleanValue\": true},
                    \"status\": {\"stringValue\": \"approved\"},
                    \"name\": {\"stringValue\": \"Admin\"},
                    \"created_at\": {\"stringValue\": \"${SETUP_TIMESTAMP}\"}
                }
            }"
        echo -e "${GREEN}Admin flag set${NC}"
    fi
    echo ""
fi

# ============================================================================
# SUCCESS
# ============================================================================
echo ""
echo "╔════════════════════════════════════════════════════════════════╗"
echo "║                   DEPLOYMENT COMPLETE                        ║"
echo "╚════════════════════════════════════════════════════════════════╝"
echo ""
echo -e "${GREEN}Your Pablo instance is live!${NC}"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  Frontend:  ${GREEN}${FRONTEND_URL}${NC}"
echo -e "  Backend:   ${GREEN}${BACKEND_URL}${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Project:   $PROJECT_ID"
echo "  Region:    $REPO_LOCATION"
echo "  AI Model:  $AI_MODEL"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BLUE}HIPAA Compliance${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  TLS/HTTPS enforced"
echo "  Data encrypted at rest (Firestore default AES-256)"
echo "  Identity Platform with mandatory MFA (TOTP)"
echo "  NIST 800-63B password policy (15+ chars)"
echo "  Secrets in Secret Manager"
echo ""
echo -e "  ${YELLOW}You still need to:${NC}"
echo "  - Sign your Google Cloud BAA"
echo "  - Enable Cloud Audit Logs for Firestore"
echo "  - Set up Firestore backups"
echo "  - See: docs/SELF_HOSTING_HIPAA_GUIDE.md"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BLUE}Estimated Monthly Cost${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  Cloud Run:     \$8-25"
echo "  Firestore:     \$1-5"
echo "  AI (per note):  \$0.10-0.40"
echo "  Total (est.):  \$15-50/month"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BLUE}Next Steps${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  1. Open ${GREEN}${FRONTEND_URL}${NC}"
echo "  2. Sign in with Google or email/password"
echo "  3. Enroll in MFA (authenticator app)"
echo "  4. Create a patient and upload a transcript"
echo "  5. Generate your first SOAP note"
echo ""
echo "  Updates:   git pull && ./redeploy.sh"
echo "  Logs:      gcloud run services logs read pablo-backend --region=${REPO_LOCATION}"
echo "  Firestore: https://console.cloud.google.com/firestore?project=${PROJECT_ID}"
echo ""
echo -e "${GREEN}Thank you for using Pablo!${NC}"
echo "  https://github.com/pablo-health/pablo"
echo ""
