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

# STEP 1: Check for billing accounts
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

# STEP 2: Create or select project
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

# STEP 3: Enable required APIs
echo ""
echo -e "${BLUE}Step 3: Enabling required APIs...${NC}"
echo ""
echo "This will enable:"
echo "  - Cloud Run (hosting)"
echo "  - Cloud Build (Docker image builds)"
echo "  - Artifact Registry (image storage)"
echo "  - Cloud SQL (PostgreSQL database)"
echo "  - Secret Manager (API keys and secrets)"
echo "  - Cloud Logging (HIPAA audit logs)"
echo "  - Vertex AI (SOAP note generation with Gemini)"
echo "  - Identity Platform (authentication with MFA)"
echo ""

gcloud services enable \
    run.googleapis.com \
    artifactregistry.googleapis.com \
    sqladmin.googleapis.com \
    secretmanager.googleapis.com \
    logging.googleapis.com \
    aiplatform.googleapis.com \
    cloudresourcemanager.googleapis.com \
    serviceusage.googleapis.com \
    identitytoolkit.googleapis.com \
    firebase.googleapis.com \
    cloudbilling.googleapis.com \
    cloudtasks.googleapis.com \
    storage.googleapis.com \
    batch.googleapis.com \
    --quiet

echo -e "${GREEN}APIs enabled${NC}"

# STEP 3b: Configure permissions
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
    --role="roles/cloudsql.client" \
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

# STEP 4: Create Artifact Registry repository
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

# STEP 5: Create Cloud SQL PostgreSQL database
echo ""
echo -e "${BLUE}Step 5: Setting up Cloud SQL PostgreSQL database...${NC}"
echo ""

SQL_INSTANCE_NAME="pablo"
SQL_DB_NAME="pablo"
SQL_DB_USER="pablo"
CONNECTION_NAME="${PROJECT_ID}:${REPO_LOCATION}:${SQL_INSTANCE_NAME}"

# Check if Cloud SQL instance already exists
INSTANCE_EXISTS=$(gcloud sql instances list \
    --filter="name=${SQL_INSTANCE_NAME}" \
    --format="value(name)" 2>/dev/null || echo "")

if [ -n "$INSTANCE_EXISTS" ]; then
    echo -e "${GREEN}Cloud SQL instance '${SQL_INSTANCE_NAME}' already exists${NC}"
else
    echo "Creating Cloud SQL PostgreSQL instance..."
    echo "  Instance:  ${SQL_INSTANCE_NAME}"
    echo "  Version:   PostgreSQL 16"
    echo "  Tier:      db-f1-micro"
    echo "  Region:    ${REPO_LOCATION}"
    echo "  Disk:      10GB (auto-increase enabled)"
    echo "  Backups:   daily at 08:00 UTC, 30-day retention, PITR 7 days"
    echo ""
    echo -e "${YELLOW}This takes 5-10 minutes...${NC}"
    echo ""

    # Backup config is HIPAA-relevant — daily backups + 7-day PITR give a
    # defensible RPO for PHI under §164.308(a)(7)(ii)(A). Applied here at
    # create time and re-enforced below via `instances patch` so re-running
    # setup against an existing instance heals misconfiguration.
    if gcloud sql instances create "$SQL_INSTANCE_NAME" \
        --database-version=POSTGRES_16 \
        --edition=ENTERPRISE \
        --tier=db-f1-micro \
        --region="$REPO_LOCATION" \
        --storage-size=10 \
        --storage-auto-increase \
        --assign-ip \
        --backup-start-time=08:00 \
        --backup-location="$REPO_LOCATION" \
        --enable-point-in-time-recovery \
        --retained-backups-count=30 \
        --retained-transaction-log-days=7 \
        --quiet 2>&1; then
        echo -e "${GREEN}Cloud SQL instance created${NC}"
    else
        echo -e "${RED}Cloud SQL instance creation failed!${NC}"
        echo ""
        echo "Please create the instance manually:"
        echo "  1. Visit: https://console.cloud.google.com/sql/instances?project=${PROJECT_ID}"
        echo "  2. Click 'Create Instance' > 'PostgreSQL'"
        echo "  3. Instance ID: ${SQL_INSTANCE_NAME}"
        echo "  4. Edition: Enterprise (not Enterprise Plus)"
        echo "  5. PostgreSQL 16, db-f1-micro, ${REPO_LOCATION}"
        echo "  6. Click 'Create Instance'"
        echo ""
        read -p "Press Enter after you've created the Cloud SQL instance..."
    fi
fi

# Wait for instance to be ready
echo ""
echo "Waiting for Cloud SQL instance to be ready..."
RETRY_COUNT=0
MAX_RETRIES=30

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    INSTANCE_STATE=$(gcloud sql instances describe "$SQL_INSTANCE_NAME" \
        --format="value(state)" 2>/dev/null || echo "")
    if [ "$INSTANCE_STATE" == "RUNNABLE" ]; then
        echo -e "${GREEN}Cloud SQL instance is ready${NC}"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo "  Instance state: ${INSTANCE_STATE:-pending}... ($((RETRY_COUNT * 10)) seconds)"
    sleep 10
done

if [ "$INSTANCE_STATE" != "RUNNABLE" ]; then
    echo -e "${RED}Cloud SQL instance is not ready after 5 minutes.${NC}"
    echo "Check status: gcloud sql instances describe ${SQL_INSTANCE_NAME}"
    exit 1
fi

# Enforce backup + PITR config on every run. Idempotent no-op when already
# correctly configured. Guards against instances created before this flag
# block existed, or drift from the Cloud Console.
echo ""
echo "Verifying backup configuration (HIPAA: daily backups + 7d PITR)..."
BACKUP_ENABLED=$(gcloud sql instances describe "$SQL_INSTANCE_NAME" \
    --format="value(settings.backupConfiguration.enabled)" 2>/dev/null || echo "")
PITR_ENABLED=$(gcloud sql instances describe "$SQL_INSTANCE_NAME" \
    --format="value(settings.backupConfiguration.pointInTimeRecoveryEnabled)" 2>/dev/null || echo "")

if [ "$BACKUP_ENABLED" != "True" ] || [ "$PITR_ENABLED" != "True" ]; then
    echo "  Patching instance to enable backups + PITR..."
    gcloud sql instances patch "$SQL_INSTANCE_NAME" \
        --backup-start-time=08:00 \
        --backup-location="$REPO_LOCATION" \
        --enable-point-in-time-recovery \
        --retained-backups-count=30 \
        --retained-transaction-log-days=7 \
        --quiet 2>&1 || {
            echo -e "${RED}Failed to enable backups.${NC}" >&2
            echo "  HIPAA requires daily backups. Enable manually:" >&2
            echo "  https://console.cloud.google.com/sql/instances/${SQL_INSTANCE_NAME}/backups?project=${PROJECT_ID}" >&2
            exit 1
        }
    echo -e "${GREEN}Backup configuration enforced${NC}"
else
    echo -e "${GREEN}Backups + PITR already enabled${NC}"
fi

# Generate a random password for the database user
DB_PASSWORD=$(openssl rand -base64 24 | tr -d '/+=')

# Create the pablo database user
echo ""
echo "Creating database user '${SQL_DB_USER}'..."
if gcloud sql users list --instance="$SQL_INSTANCE_NAME" --format="value(name)" 2>/dev/null | grep -q "^${SQL_DB_USER}$"; then
    echo -e "${GREEN}Database user '${SQL_DB_USER}' already exists${NC}"
    # Check if we have the password stored already
    if gcloud secrets describe pablo-db-password &>/dev/null; then
        DB_PASSWORD=$(gcloud secrets versions access latest --secret=pablo-db-password 2>/dev/null)
        echo "  Using existing password from Secret Manager"
    else
        echo -e "${YELLOW}Resetting password for existing user...${NC}"
        gcloud sql users set-password "$SQL_DB_USER" \
            --instance="$SQL_INSTANCE_NAME" \
            --password="$DB_PASSWORD" \
            --quiet
    fi
else
    gcloud sql users create "$SQL_DB_USER" \
        --instance="$SQL_INSTANCE_NAME" \
        --password="$DB_PASSWORD" \
        --quiet
    echo -e "${GREEN}Database user '${SQL_DB_USER}' created${NC}"
fi

# Create the pablo database
echo ""
echo "Creating database '${SQL_DB_NAME}'..."
DB_EXISTS=$(gcloud sql databases list --instance="$SQL_INSTANCE_NAME" \
    --filter="name=${SQL_DB_NAME}" --format="value(name)" 2>/dev/null || echo "")

if [ -n "$DB_EXISTS" ]; then
    echo -e "${GREEN}Database '${SQL_DB_NAME}' already exists${NC}"
else
    gcloud sql databases create "$SQL_DB_NAME" \
        --instance="$SQL_INSTANCE_NAME" \
        --quiet
    echo -e "${GREEN}Database '${SQL_DB_NAME}' created${NC}"
fi

# Store database password and connection URL as secrets
echo ""
echo "Storing database credentials in Secret Manager..."

# Cloud SQL Auth Proxy connection URL (used by Cloud Run)
DATABASE_URL="postgresql://${SQL_DB_USER}:${DB_PASSWORD}@/${SQL_DB_NAME}?host=/cloudsql/${CONNECTION_NAME}"

if gcloud secrets describe pablo-db-password &>/dev/null; then
    echo -e "${GREEN}pablo-db-password already exists — skipping${NC}"
else
    echo -n "$DB_PASSWORD" | gcloud secrets create pablo-db-password --data-file=-
    gcloud secrets add-iam-policy-binding pablo-db-password \
        --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet 2>/dev/null || true
    echo -e "${GREEN}pablo-db-password stored${NC}"
fi

if gcloud secrets describe pablo-database-url &>/dev/null; then
    echo -e "${GREEN}pablo-database-url already exists — updating${NC}"
    echo -n "$DATABASE_URL" | gcloud secrets versions add pablo-database-url --data-file=-
else
    echo -n "$DATABASE_URL" | gcloud secrets create pablo-database-url --data-file=-
    gcloud secrets add-iam-policy-binding pablo-database-url \
        --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet 2>/dev/null || true
    echo -e "${GREEN}pablo-database-url stored${NC}"
fi

echo ""
echo "Cloud SQL connection name: ${CONNECTION_NAME}"
echo ""

# Enable HIPAA audit logging for Cloud SQL
echo "Enabling HIPAA audit logs for Cloud SQL..."
echo -e "${YELLOW}Note: Enable Data Access audit logs in Cloud Console for full HIPAA compliance${NC}"
echo "  See: docs/SELF_HOSTING_HIPAA_GUIDE.md"
echo ""

# STEP 5b: Database schema note
echo ""
echo -e "${BLUE}Step 5b: Database schema...${NC}"
echo ""
echo -e "${GREEN}Database tables will be created automatically on first backend startup.${NC}"
echo "  The backend runs schema migrations (Alembic) during initialization."
echo "  No manual schema setup required."
echo ""

# STEP 6: Initialize Identity Platform (Firebase Auth with MFA)
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
    CREATE_RESPONSE=$(curl -s -X POST \
        "https://firebase.googleapis.com/v1beta1/projects/${PROJECT_ID}/webApps" \
        -H "Authorization: Bearer $(gcloud auth print-access-token)" \
        -H "X-Goog-User-Project: ${PROJECT_ID}" \
        -H "Content-Type: application/json" \
        -d '{"displayName": "Pablo"}')

    # Check for immediate errors (e.g. missing permissions, API not enabled)
    ERROR_MSG=$(echo "$CREATE_RESPONSE" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'error' in d:
        print(d['error'].get('message', 'Unknown error'))
except: pass
" 2>/dev/null)

    if [ -n "$ERROR_MSG" ]; then
        echo -e "${RED}Firebase web app creation failed: ${ERROR_MSG}${NC}"
        echo ""
        echo "Full response:"
        echo "$CREATE_RESPONSE"
        exit 1
    fi

    # webApps.create returns a long-running operation — poll until done
    OPERATION_NAME=$(echo "$CREATE_RESPONSE" | python3 -c "
import sys, json
try:
    print(json.load(sys.stdin).get('name', ''))
except: pass
" 2>/dev/null)

    if [ -z "$OPERATION_NAME" ]; then
        echo -e "${RED}Unexpected Firebase API response (no operation name):${NC}"
        echo "$CREATE_RESPONSE"
        exit 1
    fi

    echo "  Operation: ${OPERATION_NAME}"
    echo "  Waiting for Firebase web app to be created..."

    RETRY_COUNT=0
    MAX_RETRIES=24  # 24 * 5s = 2 minutes
    FIREBASE_APP_ID=""

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        sleep 5
        OP_STATUS=$(curl -s \
            "https://firebase.googleapis.com/v1beta1/${OPERATION_NAME}" \
            -H "Authorization: Bearer $(gcloud auth print-access-token)" \
            -H "X-Goog-User-Project: ${PROJECT_ID}")

        DONE=$(echo "$OP_STATUS" | python3 -c "
import sys, json
try:
    print('yes' if json.load(sys.stdin).get('done') else 'no')
except: print('no')
" 2>/dev/null)

        if [ "$DONE" = "yes" ]; then
            FIREBASE_APP_ID=$(echo "$OP_STATUS" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    if 'error' in d:
        print('ERROR:' + d['error'].get('message', 'Unknown error'))
    else:
        print(d.get('response', {}).get('appId', ''))
except: pass
" 2>/dev/null)

            if [[ "$FIREBASE_APP_ID" == ERROR:* ]]; then
                echo -e "${RED}Firebase web app creation failed: ${FIREBASE_APP_ID#ERROR:}${NC}"
                exit 1
            fi
            break
        fi

        RETRY_COUNT=$((RETRY_COUNT + 1))
        echo "  Still waiting... ($((RETRY_COUNT * 5))s)"
    done

    if [ -n "$FIREBASE_APP_ID" ]; then
        echo -e "${GREEN}Firebase web app created: ${FIREBASE_APP_ID}${NC}"
    else
        echo -e "${RED}Firebase web app creation timed out after 2 minutes.${NC}"
        echo "Check: https://console.firebase.google.com/project/${PROJECT_ID}/settings/general"
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

# Vertex AI (Gemini) is the default — auto-authenticated on Cloud Run, covered
# under the GCP BAA, and requires no third-party API key.
AI_MODEL="google:gemini-3.1-pro-preview"
GOOGLE_REGION="global"
ANTHROPIC_SECRET=""

# STEP 7: Generate secrets
echo ""
echo -e "${BLUE}Step 7: Generating secrets...${NC}"
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

# STEP 7b: Set up Google OAuth credentials
echo ""
echo -e "${BLUE}Step 7b: Setting up Google OAuth credentials...${NC}"
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

# STEP 8: Mirror pre-built container images from ghcr.io
echo ""
echo -e "${BLUE}Step 8: Mirroring container images from ghcr.io...${NC}"
echo ""
echo "Pablo ships pre-built backend and frontend images on GitHub Container Registry."
echo "This step copies them into your Artifact Registry for Cloud Run to deploy."
echo ""

# PABLO_VERSION can be overridden to pin a specific release (e.g. export PABLO_VERSION=v0.1.0)
PABLO_VERSION="${PABLO_VERSION:-latest}"
SOURCE_BACKEND="ghcr.io/pablo-health/backend:${PABLO_VERSION}"
SOURCE_FRONTEND="ghcr.io/pablo-health/frontend:${PABLO_VERSION}"
SOURCE_PENTEST="ghcr.io/pablo-health/pentest:${PABLO_VERSION}"
DEST_BACKEND="${REPO_LOCATION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/backend:${PABLO_VERSION}"
DEST_FRONTEND="${REPO_LOCATION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/frontend:${PABLO_VERSION}"
DEST_PENTEST="${REPO_LOCATION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/pentest:${PABLO_VERSION}"

echo "  Version:  ${PABLO_VERSION}"
echo "  Backend:  ${SOURCE_BACKEND}"
echo "  Frontend: ${SOURCE_FRONTEND}"
echo "  Pentest:  ${SOURCE_PENTEST}"
echo ""

if ! command -v docker &>/dev/null; then
    echo -e "${RED}docker is required to mirror container images.${NC}"
    echo "Cloud Shell includes it by default. Locally, install Docker Desktop:"
    echo "  https://docs.docker.com/get-docker/"
    exit 1
fi

gcloud auth configure-docker "${REPO_LOCATION}-docker.pkg.dev" --quiet

echo -e "${YELLOW}Mirroring backend image...${NC}"
docker pull --platform linux/amd64 "$SOURCE_BACKEND"
docker tag "$SOURCE_BACKEND" "$DEST_BACKEND"
docker push "$DEST_BACKEND"
echo -e "${GREEN}Backend image mirrored${NC}"

echo ""
echo -e "${YELLOW}Mirroring frontend image...${NC}"
docker pull --platform linux/amd64 "$SOURCE_FRONTEND"
docker tag "$SOURCE_FRONTEND" "$DEST_FRONTEND"
docker push "$DEST_FRONTEND"
echo -e "${GREEN}Frontend image mirrored${NC}"
echo ""

# Pentest image extends the backend image and powers the weekly pentest
# Cloud Run Job in Step 11. Skip gracefully if the release didn't publish
# one (e.g. pinned to a pre-pentest version) — Step 11 degrades cleanly.
echo -e "${YELLOW}Mirroring pentest image...${NC}"
if docker pull --platform linux/amd64 "$SOURCE_PENTEST" 2>/dev/null; then
    docker tag "$SOURCE_PENTEST" "$DEST_PENTEST"
    docker push "$DEST_PENTEST"
    echo -e "${GREEN}Pentest image mirrored${NC}"
else
    echo -e "${YELLOW}Pentest image not published at ${PABLO_VERSION} — skipping (weekly pentest job will be skipped in Step 11)${NC}"
fi
echo ""

# STEP 9: Deploy to Cloud Run
echo ""
echo -e "${BLUE}Step 9: Deploying to Cloud Run...${NC}"
echo ""

# Build secrets string for backend
SECRETS_STRING="JWT_SECRET_KEY=JWT_SECRET_KEY:latest,DATABASE_URL=pablo-database-url:latest"
if [ -n "$ANTHROPIC_SECRET" ]; then
    SECRETS_STRING="${SECRETS_STRING},${ANTHROPIC_SECRET}"
fi

# Deploy backend
echo -e "${YELLOW}Deploying backend...${NC}"
gcloud run deploy pablo-backend \
    --image="$DEST_BACKEND" \
    --region="$REPO_LOCATION" \
    --platform=managed \
    --allow-unauthenticated \
    --service-account="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --memory=4Gi \
    --cpu=2 \
    --min-instances=1 \
    --max-instances=1 \
    --timeout=300 \
    --set-secrets="${SECRETS_STRING}" \
    --add-cloudsql-instances="${CONNECTION_NAME}" \
    --set-env-vars="^||^GCP_PROJECT_ID=${PROJECT_ID}||FIREBASE_PROJECT_ID=${PROJECT_ID}||ENVIRONMENT=production||ENFORCE_HTTPS=true||AI_MODEL=${AI_MODEL}||GOOGLE_CLOUD_PROJECT=${PROJECT_ID}||GOOGLE_REGION=${GOOGLE_REGION:-global}||GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}||TRUSTED_PROXY_IPS=*||DATABASE_BACKEND=postgres||TRANSCRIPTION_PROVIDER=assemblyai||TRANSCRIPTION_ENABLED=true||GOOGLE_GENAI_USE_VERTEXAI=true||VERTEX_REGION=global||RESTRICT_SIGNUPS=true" \
    --quiet

BACKEND_URL=$(gcloud run services describe pablo-backend \
    --region="$REPO_LOCATION" \
    --format="value(status.url)")

echo -e "${GREEN}Backend deployed: ${BACKEND_URL}${NC}"
echo ""

# Deploy frontend
echo -e "${YELLOW}Deploying frontend...${NC}"
gcloud run deploy pablo-frontend \
    --image="$DEST_FRONTEND" \
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

# Add frontend domain to Identity Platform + update CORS

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

# Update backend CORS + OIDC audience + blocking-function caller SA.
# The backend pins these on every /api/ext/auth call to block tokens from
# any other service account in any other GCP project. See
# backend/app/routes/ext_auth.py:_verify_blocking_function_token.
echo "Updating backend CORS + OIDC verification config..."

# Discover the Firebase blocking function runtime SA. One `functions list`
# call (filtered by name) returns whatever blocking function is actually
# deployed with its real SA — no casing guesses. Fall back to the default
# Cloud Functions gen2 compute SA if none exists.
#
# Wrapped in a 10s watchdog: GCP API calls occasionally hang (not fail),
# and macOS lacks `timeout(1)`, so we DIY it with a backgrounded child.
BLOCKING_FN_SA=""
echo "  Looking up deployed blocking function (10s max)..."
LIST_TMP=$(mktemp)
gcloud functions list \
    --gen2 \
    --regions="$REPO_LOCATION" \
    --filter="name~(beforeCreate|beforeSignIn|beforecreate|beforesignin)$" \
    --format="value(serviceConfig.serviceAccountEmail)" \
    --limit=1 >"$LIST_TMP" 2>/dev/null &
GPID=$!
( sleep 10; kill -TERM "$GPID" 2>/dev/null ) &
WPID=$!
wait "$GPID" 2>/dev/null || true
kill "$WPID" 2>/dev/null || true
wait "$WPID" 2>/dev/null || true
BLOCKING_FN_SA=$(head -n 1 "$LIST_TMP" 2>/dev/null || echo "")
rm -f "$LIST_TMP"

if [ -n "$BLOCKING_FN_SA" ]; then
    echo "  Blocking function SA (from deployed function): $BLOCKING_FN_SA"
fi

if [ -z "$BLOCKING_FN_SA" ]; then
    PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" \
        --format="value(projectNumber)" 2>/dev/null || echo "")
    if [ -n "$PROJECT_NUMBER" ]; then
        BLOCKING_FN_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
        echo "  Blocking function SA (default gen2 compute): $BLOCKING_FN_SA"
        echo -e "  ${YELLOW}If you deploy blocking functions with a custom SA, update BLOCKING_FUNCTION_SERVICE_ACCOUNT on pablo-backend.${NC}"
    else
        echo -e "  ${YELLOW}Could not determine blocking function SA — skipping caller check.${NC}"
        echo -e "  ${YELLOW}Set BLOCKING_FUNCTION_SERVICE_ACCOUNT on pablo-backend once the SA is known.${NC}"
    fi
fi

ENV_VAR_UPDATES="CORS_ORIGINS=${FRONTEND_URL},BACKEND_BASE_URL=${BACKEND_URL}"
if [ -n "$BLOCKING_FN_SA" ]; then
    ENV_VAR_UPDATES="${ENV_VAR_UPDATES},BLOCKING_FUNCTION_SERVICE_ACCOUNT=${BLOCKING_FN_SA}"
fi

gcloud run services update pablo-backend \
    --region="$REPO_LOCATION" \
    --update-env-vars="${ENV_VAR_UPDATES}" \
    --quiet

echo -e "${GREEN}CORS + OIDC verification configured${NC}"

# STEP 10: Create your user
echo ""
echo -e "${BLUE}Step 10: Creating your user account...${NC}"
echo ""

# Wait for backend to be healthy
echo "Waiting for backend to be healthy..."
RETRY_COUNT=0
MAX_RETRIES=12

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    HEALTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "${BACKEND_URL}/api/health" 2>/dev/null || echo "000")
    if [ "$HEALTH_STATUS" == "200" ]; then
        echo -e "${GREEN}Backend is healthy${NC}"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo "  Waiting for backend... ($((RETRY_COUNT * 5)) seconds)"
    sleep 5
done

if [ "$HEALTH_STATUS" != "200" ]; then
    echo -e "${YELLOW}Backend health check timed out — continuing anyway${NC}"
fi
echo ""

# Check if an admin user already exists via the backend API
AUTH_TOKEN=$(gcloud auth print-access-token)
EXISTING_ADMIN=""

# Try to check for existing admin via Cloud SQL. Use psql's unaligned
# tuples-only mode so the output is just the email — no header row, no
# row-count footer, no leading whitespace for a regex to trip over.
EXISTING_ADMIN=$(gcloud sql connect "$SQL_INSTANCE_NAME" --database="$SQL_DB_NAME" --user="$SQL_DB_USER" --quiet 2>/dev/null <<SQL || echo ""
\pset format unaligned
\pset tuples_only on
SELECT email FROM users WHERE is_admin = true LIMIT 1;
SQL
)
EXISTING_ADMIN=$(echo "$EXISTING_ADMIN" | grep -Eo '[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}' | head -1 || echo "")

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

    # Create user via Identity Platform
    echo "Creating user account in Identity Platform..."
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
        echo -e "${GREEN}User created in Identity Platform (uid: ${ADMIN_UID})${NC}"

        # Validate email and uid format before interpolating into SQL.
        # Admin bootstrap runs once at install time over Cloud SQL IAM — the
        # only caller with cloudsql.instances.connect on this project is the
        # operator. Validation here is belt-and-suspenders against a mistyped
        # or maliciously crafted email/UID.
        if ! [[ "$ADMIN_EMAIL_LOWER" =~ ^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$ ]]; then
            echo -e "${RED}Invalid admin email format: ${ADMIN_EMAIL_LOWER}${NC}" >&2
            exit 1
        fi
        if ! [[ "$ADMIN_UID" =~ ^[A-Za-z0-9_-]+$ ]]; then
            echo -e "${RED}Invalid Identity Platform UID format: ${ADMIN_UID}${NC}" >&2
            exit 1
        fi

        # Seed admin via Cloud SQL IAM. This is the authenticated bootstrap
        # path — only the operator running setup (who holds
        # cloudsql.instances.connect on this project) can perform it. An
        # HTTP bootstrap endpoint would have to accept unauthenticated
        # writes during a first-run window; we avoid that trust boundary.
        echo "Setting admin flag in database..."
        SETUP_TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)

        if gcloud sql connect "$SQL_INSTANCE_NAME" \
            --database="$SQL_DB_NAME" \
            --user="$SQL_DB_USER" \
            --quiet 2>/dev/null <<SQL
INSERT INTO allowed_emails (email, added_by, added_at)
VALUES ('${ADMIN_EMAIL_LOWER}', 'setup-solo.sh', '${SETUP_TIMESTAMP}')
ON CONFLICT (email) DO NOTHING;

INSERT INTO users (id, email, is_admin, status, name, created_at)
VALUES ('${ADMIN_UID}', '${ADMIN_EMAIL_LOWER}', true, 'approved', 'Admin', '${SETUP_TIMESTAMP}')
ON CONFLICT (id) DO NOTHING;
SQL
        then
            echo -e "${GREEN}Admin user created in database${NC}"
        else
            echo -e "${YELLOW}Could not seed admin via Cloud SQL. Connect manually with:${NC}" >&2
            echo "  gcloud sql connect ${SQL_INSTANCE_NAME} --database=${SQL_DB_NAME} --user=${SQL_DB_USER}" >&2
            exit 1
        fi
    else
        echo -e "${YELLOW}Could not create user (may already exist).${NC}"
        echo "  If re-running setup, the existing user is unchanged."
    fi
    echo ""
fi

# STEP 11: Compliance bucket + scheduled routines (HIPAA log review + pentest)
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}STEP 11: HIPAA compliance routines (optional, recommended)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Schedules two Cloud Run Jobs:"
echo "  - hipaa-log-review (daily 07:00 local) — reviews audit log for anomalies"
echo "  - pentest          (weekly Sun 02:00) — owner-authorized pentest"
echo "Reports land in gs://${PROJECT_ID}-compliance-reports/ with 7yr retention lock."
echo ""
read -p "Enable HIPAA compliance routines? [Y/n]: " ENABLE_ROUTINES
ENABLE_ROUTINES="${ENABLE_ROUTINES:-Y}"

if [[ "$ENABLE_ROUTINES" =~ ^[Yy]$ ]]; then
    gcloud services enable \
        run.googleapis.com \
        cloudscheduler.googleapis.com \
        storage.googleapis.com \
        aiplatform.googleapis.com \
        --project="$PROJECT_ID" --quiet >/dev/null

    COMPLIANCE_BUCKET="${PROJECT_ID}-compliance-reports"
    if ! gcloud storage buckets describe "gs://${COMPLIANCE_BUCKET}" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating compliance-reports bucket with 7-year retention lock"
        gcloud storage buckets create "gs://${COMPLIANCE_BUCKET}" \
            --project="$PROJECT_ID" \
            --location="$REPO_LOCATION" \
            --uniform-bucket-level-access >/dev/null
        gcloud storage buckets update "gs://${COMPLIANCE_BUCKET}" \
            --retention-period=7y >/dev/null
        echo -e "${GREEN}    Bucket created${NC} (run 'gcloud storage buckets update --lock-retention-period' when you're ready to make it irreversible)"
    else
        echo "  Compliance bucket already exists"
    fi

    BACKEND_IMAGE="${REPO_LOCATION}-docker.pkg.dev/${PROJECT_ID}/pablo/backend:${PABLO_VERSION:-latest}"
    PENTEST_IMAGE="${REPO_LOCATION}-docker.pkg.dev/${PROJECT_ID}/pablo/pentest:${PABLO_VERSION:-latest}"
    # Run compliance jobs as the same SA the backend Cloud Run service uses.
    # setup-solo deploys the backend on the default gen2 compute SA
    # (${PROJECT_NUMBER}-compute@), not a dedicated pablo-backend@ SA —
    # keeping the jobs on the same identity avoids a second actAs grant.
    BACKEND_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

    # Grant the backend SA access to read audit_logs and write to the bucket
    gcloud storage buckets add-iam-policy-binding "gs://${COMPLIANCE_BUCKET}" \
        --member="serviceAccount:${BACKEND_SA}" \
        --role="roles/storage.objectCreator" --condition=None >/dev/null 2>&1 || true

    # HIPAA log review job (daily)
    if ! gcloud run jobs describe hipaa-log-review --region="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Deploying Cloud Run Job: hipaa-log-review"
        gcloud run jobs create hipaa-log-review \
            --project="$PROJECT_ID" \
            --region="$REPO_LOCATION" \
            --image="$BACKEND_IMAGE" \
            --service-account="$BACKEND_SA" \
            --set-env-vars="COMPLIANCE_REPORT_BUCKET=${COMPLIANCE_BUCKET},GCP_PROJECT_ID=${PROJECT_ID},VERTEX_REGION=us-east5,REVIEW_WINDOW_HOURS=24" \
            --set-secrets="DATABASE_URL=pablo-database-url:latest" \
            --command="python3.13" \
            --args="-m,backend.app.jobs.hipaa_log_review" \
            --max-retries=2 --task-timeout=15m >/dev/null
    else
        echo "  Cloud Run Job hipaa-log-review already exists"
    fi

    # HIPAA log review job (monthly rollup) — same image, 30-day window
    if ! gcloud run jobs describe hipaa-log-review-monthly --region="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Deploying Cloud Run Job: hipaa-log-review-monthly"
        gcloud run jobs create hipaa-log-review-monthly \
            --project="$PROJECT_ID" \
            --region="$REPO_LOCATION" \
            --image="$BACKEND_IMAGE" \
            --service-account="$BACKEND_SA" \
            --set-env-vars="COMPLIANCE_REPORT_BUCKET=${COMPLIANCE_BUCKET},GCP_PROJECT_ID=${PROJECT_ID},VERTEX_REGION=us-east5,REVIEW_WINDOW_HOURS=720" \
            --set-secrets="DATABASE_URL=pablo-database-url:latest" \
            --command="python3.13" \
            --args="-m,backend.app.jobs.hipaa_log_review" \
            --max-retries=2 --task-timeout=30m >/dev/null
    else
        echo "  Cloud Run Job hipaa-log-review-monthly already exists"
    fi

    # Weekly pipeline heartbeat
    if ! gcloud run jobs describe pipeline-heartbeat --region="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Deploying Cloud Run Job: pipeline-heartbeat"
        gcloud run jobs create pipeline-heartbeat \
            --project="$PROJECT_ID" \
            --region="$REPO_LOCATION" \
            --image="$BACKEND_IMAGE" \
            --service-account="$BACKEND_SA" \
            --set-env-vars="COMPLIANCE_REPORT_BUCKET=${COMPLIANCE_BUCKET}" \
            --command="python3.13" \
            --args="-m,backend.app.jobs.pipeline_heartbeat" \
            --max-retries=1 --task-timeout=2m >/dev/null
    else
        echo "  Cloud Run Job pipeline-heartbeat already exists"
    fi

    # On-demand HIPAA attestation document generator
    if ! gcloud run jobs describe hipaa-attestation --region="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Deploying Cloud Run Job: hipaa-attestation"
        gcloud run jobs create hipaa-attestation \
            --project="$PROJECT_ID" \
            --region="$REPO_LOCATION" \
            --image="$BACKEND_IMAGE" \
            --service-account="$BACKEND_SA" \
            --set-env-vars="COMPLIANCE_REPORT_BUCKET=${COMPLIANCE_BUCKET},GCP_PROJECT_ID=${PROJECT_ID},PABLO_VERSION=${PABLO_VERSION:-unknown}" \
            --set-secrets="DATABASE_URL=pablo-database-url:latest" \
            --command="python3.13" \
            --args="-m,backend.app.jobs.hipaa_attestation" \
            --max-retries=1 --task-timeout=5m >/dev/null
    else
        echo "  Cloud Run Job hipaa-attestation already exists"
    fi

    # Pentest identity SA — dedicated, minimum-privilege SA for the pentest
    # Cloud Run Job. The job creates ephemeral MFA-enrolled Firebase users on
    # each run (see backend/app/jobs/pentest_identity.py), which requires
    # roles/firebaseauth.admin. Granting that to the shared compute SA would
    # broaden blast radius to every Cloud Run service in the project, so we
    # bind it to a dedicated SA used only by the pentest Job.
    # See docs/compliance/pentest-identity-sa.md for the rationale.
    PENTEST_SA_NAME="pentest-identity"
    PENTEST_SA="${PENTEST_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
    if ! gcloud iam service-accounts describe "$PENTEST_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating pentest identity service account (dedicated, least-privilege)"
        gcloud iam service-accounts create "$PENTEST_SA_NAME" \
            --project="$PROJECT_ID" \
            --display-name="Pablo pentest identity bootstrap" \
            --description="Creates, enrolls TOTP on, and deletes ephemeral Firebase users used by the automated pentest. Bound to the 'pentest' Cloud Run Job only — do not reuse." >/dev/null

        # IAM has two consistency horizons: `describe` can succeed well
        # before `add-iam-policy-binding` sees the SA. Keep a 15s floor
        # (observed failure on pablohealth-prod without it).
        echo "  Waiting for pentest SA to propagate..."
        sleep 15
    else
        echo "  Pentest identity SA already exists"
    fi

    # Grant *only* Firebase Auth admin. No project-wide owner/editor.
    # Retry on the transient "does not exist" race just after creation.
    for attempt in 1 2 3 4 5; do
        if gcloud projects add-iam-policy-binding "$PROJECT_ID" \
            --member="serviceAccount:${PENTEST_SA}" \
            --role="roles/firebaseauth.admin" --condition=None >/dev/null 2>&1; then
            break
        fi
        [[ $attempt -eq 5 ]] && { echo -e "${RED}  ✗ Pentest SA binding failed${NC}"; exit 1; }
        sleep $((attempt * 5))
    done

    # Pentest job (uses the pentest image which extends backend image)
    if ! gcloud run jobs describe pentest --region="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        if gcloud artifacts docker images describe "$PENTEST_IMAGE" >/dev/null 2>&1; then
            echo "  Deploying Cloud Run Job: pentest (as $PENTEST_SA)"
            gcloud run jobs create pentest \
                --project="$PROJECT_ID" \
                --region="$REPO_LOCATION" \
                --image="$PENTEST_IMAGE" \
                --service-account="$PENTEST_SA" \
                --set-env-vars="COMPLIANCE_REPORT_BUCKET=${COMPLIANCE_BUCKET},GCP_PROJECT_ID=${PROJECT_ID},VERTEX_REGION=us-east5" \
                --max-retries=0 --task-timeout=50m >/dev/null
            # Bucket write access for the pentest report upload.
            gcloud storage buckets add-iam-policy-binding "gs://${COMPLIANCE_BUCKET}" \
                --member="serviceAccount:${PENTEST_SA}" \
                --role="roles/storage.objectCreator" --condition=None >/dev/null 2>&1 || true
        else
            echo -e "${YELLOW}  Pentest image not found in Artifact Registry — skipping Cloud Run Job.${NC}"
            echo -e "${YELLOW}  Mirrored in Step 8 from ghcr.io/pablo-health/pentest:\${PABLO_VERSION}; re-run setup-solo pinned to a release that publishes it.${NC}"
        fi
    else
        # Job already exists — re-bind to the dedicated SA in case an older
        # setup run created it on the shared compute SA. Idempotent.
        CURRENT_PENTEST_SA=$(gcloud run jobs describe pentest \
            --project="$PROJECT_ID" --region="$REPO_LOCATION" \
            --format="value(spec.template.spec.template.spec.serviceAccountName)" 2>/dev/null || echo "")
        if [[ "$CURRENT_PENTEST_SA" != "$PENTEST_SA" ]]; then
            echo "  Migrating pentest Job from '${CURRENT_PENTEST_SA:-<default>}' to $PENTEST_SA"
            gcloud run jobs update pentest \
                --project="$PROJECT_ID" --region="$REPO_LOCATION" \
                --service-account="$PENTEST_SA" >/dev/null
            gcloud storage buckets add-iam-policy-binding "gs://${COMPLIANCE_BUCKET}" \
                --member="serviceAccount:${PENTEST_SA}" \
                --role="roles/storage.objectCreator" --condition=None >/dev/null 2>&1 || true
        else
            echo "  Cloud Run Job pentest already exists (on dedicated SA)"
        fi
    fi

    # Cloud Scheduler triggers
    SCHEDULER_SA="${SCHEDULER_SA:-pablo-scheduler@${PROJECT_ID}.iam.gserviceaccount.com}"
    if ! gcloud iam service-accounts describe "$SCHEDULER_SA" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating scheduler service account"
        gcloud iam service-accounts create pablo-scheduler \
            --project="$PROJECT_ID" \
            --display-name="Pablo Cloud Scheduler" >/dev/null

        # IAM has eventual consistency — the SA isn't immediately visible to
        # policy bindings. Poll until describe succeeds (up to ~30s) before
        # attempting the binding, or the very next gcloud call will fail with
        # "Service account ... does not exist".
        echo "  Waiting for scheduler SA to propagate..."
        for i in 1 2 3 4 5 6 7 8 9 10; do
            if gcloud iam service-accounts describe "$SCHEDULER_SA" \
                --project="$PROJECT_ID" >/dev/null 2>&1; then
                break
            fi
            sleep 3
        done
    fi

    # Always (re)apply the invoker binding — gcloud treats this as idempotent,
    # and gating it on the create path above would leave re-runs broken if the
    # binding ever failed after the SA was created (as it did once for you).
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:${SCHEDULER_SA}" \
        --role="roles/run.invoker" --condition=None >/dev/null

    USER_TZ="${USER_TIMEZONE:-America/Los_Angeles}"
    JOB_RUN_URL_BASE="https://${REPO_LOCATION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs"

    if ! gcloud scheduler jobs describe hipaa-log-review-daily --location="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating scheduler: hipaa-log-review-daily (07:00 $USER_TZ)"
        gcloud scheduler jobs create http hipaa-log-review-daily \
            --project="$PROJECT_ID" \
            --location="$REPO_LOCATION" \
            --schedule="0 7 * * *" \
            --time-zone="$USER_TZ" \
            --uri="${JOB_RUN_URL_BASE}/hipaa-log-review:run" \
            --http-method=POST \
            --oauth-service-account-email="$SCHEDULER_SA" >/dev/null
    fi

    if ! gcloud scheduler jobs describe hipaa-log-review-monthly --location="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating scheduler: hipaa-log-review-monthly (1st of month, 06:00 $USER_TZ)"
        gcloud scheduler jobs create http hipaa-log-review-monthly \
            --project="$PROJECT_ID" \
            --location="$REPO_LOCATION" \
            --schedule="0 6 1 * *" \
            --time-zone="$USER_TZ" \
            --uri="${JOB_RUN_URL_BASE}/hipaa-log-review-monthly:run" \
            --http-method=POST \
            --oauth-service-account-email="$SCHEDULER_SA" >/dev/null
    fi

    if ! gcloud scheduler jobs describe pipeline-heartbeat-weekly --location="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating scheduler: pipeline-heartbeat-weekly (Mon 09:00 $USER_TZ)"
        gcloud scheduler jobs create http pipeline-heartbeat-weekly \
            --project="$PROJECT_ID" \
            --location="$REPO_LOCATION" \
            --schedule="0 9 * * 1" \
            --time-zone="$USER_TZ" \
            --uri="${JOB_RUN_URL_BASE}/pipeline-heartbeat:run" \
            --http-method=POST \
            --oauth-service-account-email="$SCHEDULER_SA" >/dev/null
    fi

    if ! gcloud scheduler jobs describe hipaa-attestation-quarterly --location="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating scheduler: hipaa-attestation-quarterly (1st of Jan/Apr/Jul/Oct 05:00 $USER_TZ)"
        gcloud scheduler jobs create http hipaa-attestation-quarterly \
            --project="$PROJECT_ID" \
            --location="$REPO_LOCATION" \
            --schedule="0 5 1 1,4,7,10 *" \
            --time-zone="$USER_TZ" \
            --uri="${JOB_RUN_URL_BASE}/hipaa-attestation:run" \
            --http-method=POST \
            --oauth-service-account-email="$SCHEDULER_SA" >/dev/null
    fi

    if ! gcloud scheduler jobs describe pentest-weekly --location="$REPO_LOCATION" --project="$PROJECT_ID" >/dev/null 2>&1; then
        echo "  Creating scheduler: pentest-weekly (Sunday 02:00 $USER_TZ)"
        gcloud scheduler jobs create http pentest-weekly \
            --project="$PROJECT_ID" \
            --location="$REPO_LOCATION" \
            --schedule="0 2 * * 0" \
            --time-zone="$USER_TZ" \
            --uri="${JOB_RUN_URL_BASE}/pentest:run" \
            --http-method=POST \
            --oauth-service-account-email="$SCHEDULER_SA" >/dev/null
    fi

    echo -e "${GREEN}  Routines enabled${NC}"
else
    echo "  Skipped. Run ./scripts/routines/setup-routines.sh later to enable."
fi

# STEP 12: Monitoring (GCP-native, optional but recommended)
echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}STEP 12: Monitoring (optional, recommended)${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "Adds Cloud Monitoring uptime checks + alert policies with email"
echo "notifications. No third-party accounts required; stays under your"
echo "existing GCP BAA."
echo ""
read -p "Enable GCP monitoring? [Y/n]: " ENABLE_MONITORING
ENABLE_MONITORING="${ENABLE_MONITORING:-Y}"

if [[ "$ENABLE_MONITORING" =~ ^[Yy]$ ]]; then
    read -p "  Email for alerts: " ALERT_EMAIL
    if [[ -n "$ALERT_EMAIL" ]]; then
        ./scripts/monitoring/setup.sh \
            "$PROJECT_ID" "$BACKEND_URL" "$FRONTEND_URL" "$ALERT_EMAIL"
    else
        echo "  No email provided; skipped. Run scripts/monitoring/setup.sh later."
    fi
else
    echo "  Skipped. Run scripts/monitoring/setup.sh later to enable."
fi

# SUCCESS
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
echo "  Database:  Cloud SQL PostgreSQL (${CONNECTION_NAME})"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BLUE}HIPAA Compliance${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "  TLS/HTTPS enforced"
echo "  Data encrypted at rest (Cloud SQL default AES-256)"
echo "  Identity Platform with mandatory MFA (TOTP)"
echo "  NIST 800-63B password policy (15+ chars)"
echo "  Secrets in Secret Manager"
echo ""
echo -e "  ${YELLOW}You still need to:${NC}"
echo "  - Sign your Google Cloud BAA"
echo "  - Enable Cloud Audit Logs for Cloud SQL"
echo "  - Enable Cloud SQL automated backups"
echo "  - See: docs/SELF_HOSTING_HIPAA_GUIDE.md"
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
echo "  Database:  https://console.cloud.google.com/sql/instances/pablo?project=${PROJECT_ID}"
echo ""
echo -e "${GREEN}Thank you for using Pablo!${NC}"
echo "  https://github.com/pablo-health/pablo"
echo ""
