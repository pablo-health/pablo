# Google Cloud Platform Deployment Guide

## Overview

This guide covers deploying the Pablo on Google Cloud Platform (GCP) in two scenarios:

1. **Individual Therapist Deployment** - Each therapist deploys on their own GCP account (recommended for pilot)

Both scenarios leverage our existing multi-tenant architecture and HIPAA compliance features.

## Table of Contents

- [Prerequisites](#prerequisites)
- [Scenario 1: Individual Therapist Deployment](#scenario-1-individual-therapist-deployment)
  - [Architecture](#architecture)
  - [Cost Estimate](#cost-estimate)
  - [Quick Start](#quick-start)
  - [Manual Deployment](#manual-deployment)
  - [Architecture](#saas-architecture)
  - [Cost Model](#saas-cost-model)
  - [Provisioning](#saas-provisioning)
- [HIPAA Compliance](#hipaa-compliance)
- [Troubleshooting](#troubleshooting)
- [Monitoring & Maintenance](#monitoring--maintenance)

---

## Prerequisites

### For Individual Deployment

- Google Cloud account with billing enabled
- `gcloud` CLI installed (or use Cloud Shell)
- Git repository access
- Domain name (optional, for custom domain)

### For SaaS Deployment

All of the above, plus:
- Subscription management system (Stripe recommended)
- Admin dashboard for managing therapist instances
- BAA signed with Google Cloud

---

## Scenario 1: Individual Therapist Deployment

### Architecture

```
Therapist's GCP Account
├── Cloud Run - Backend (FastAPI)
│   ├── Firestore connection
│   ├── Vertex AI for SOAP generation
│   └── Secret Manager access
├── Cloud Run - Frontend (Next.js)
│   └── Proxies API requests to backend
├── Firestore Database
│   ├── Patients collection
│   ├── Sessions collection
│   └── SOAP notes collection
├── Firebase Authentication
│   ├── Google OAuth provider
│   └── Admin user(s)
├── Secret Manager
│   ├── JWT_SECRET_KEY
│   └── AI API keys (if using Anthropic/OpenAI)
└── Cloud Audit Logs
    └── HIPAA compliance tracking
```

### Key Characteristics

- **Billing**: Therapist pays their own GCP costs (~$21-68/month)
- **Data Sovereignty**: Complete control over patient data
- **Isolation**: Zero shared infrastructure with other therapists
- **HIPAA**: No BAA with platform owner needed (self-hosted)
- **Scalability**: 1-100 patients, 10-1000 sessions per therapist

### Cost Estimate

**Monthly Costs** (Individual Therapist):

| Service | Usage | Cost |
|---------|-------|------|
| Cloud Run (Backend) | 0.5-2 GB RAM, 50-200 hrs/mo | $5-15 |
| Cloud Run (Frontend) | 0.5 GB RAM, always-on | $3-10 |
| Firestore | 100-500 patients, 1K-5K sessions | $1-5 |
| Cloud Audit Logs | HIPAA compliance logging | $1-3 |
| Vertex AI (Gemini) | 100-300 SOAP notes/month | $10-30 |
| Egress | API responses, exports | $1-5 |
| **Total** | | **$21-68/month** |

**Free Tier Benefits**:
- Cloud Run: 2M requests/month free
- Firestore: 1 GB storage, 50K reads, 20K writes/day free
- Cloud Build: 120 build-minutes/day free

**Typical therapist usage**: ~$35/month (median)

### Quick Start

#### Option 1: Cloud Shell (Recommended for Non-Technical Users)

Click this button to deploy from Cloud Shell:

The wizard will:
1. Check prerequisites (billing account, gcloud setup)
2. Create a new GCP project (or use existing)
3. Enable required APIs
4. Set up Firestore and Firebase Auth
5. Build and deploy both services
6. Configure secrets and environment variables
7. Create your admin user
8. Output your application URLs

**Estimated time**: 15-30 minutes

#### Option 2: Local Deployment Script

```bash
# Clone the repository
git clone https://github.com/pablo-health/pablo.git
cd pablo

# The script will be generated during Cloud Shell deployment
./setup-solo.sh
```

### Manual Deployment

If you prefer step-by-step control or need to customize the deployment:

#### Step 1: Create GCP Project

```bash
# Set project ID (must be globally unique)
export PROJECT_ID="pablo-$(date +%s)"
export REGION="us-central1"

# Create project
gcloud projects create $PROJECT_ID --name="Pablo"

# Set as active project
gcloud config set project $PROJECT_ID

# Link billing account (get billing account ID from console)
export BILLING_ACCOUNT_ID="YOUR_BILLING_ACCOUNT_ID"
gcloud billing projects link $PROJECT_ID --billing-account=$BILLING_ACCOUNT_ID
```

#### Step 2: Enable Required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  firestore.googleapis.com \
  firebase.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com \
  aiplatform.googleapis.com
```

**Wait 2-5 minutes** for APIs to fully enable before proceeding.

#### Step 3: Create Firestore Database

```bash
# Create Firestore in Native mode (nam5 = North America)
gcloud firestore databases create \
  --location=nam5 \
  --type=firestore-native

# Enable audit logs for HIPAA compliance
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$PROJECT_ID@appspot.gserviceaccount.com" \
  --role="roles/logging.logWriter"
```

#### Step 3b: Deploy Firestore Indexes

Firestore requires composite indexes for multi-field queries. The application uses indexes for:

**Patient Queries:**
- Search by last name with sorting
- Search by first name with sorting

**Session Queries:**
- List sessions by user (sorted by date)
- List sessions by patient (sorted by date)
- Get next session number for patient

**Deploy indexes:**

```bash
# Option 1: Using Firebase CLI (recommended)
firebase deploy --only firestore:indexes --project=$PROJECT_ID

# Option 2: Manual creation via Cloud Console
# Visit: https://console.cloud.google.com/firestore/indexes?project=$PROJECT_ID
```

**Index Configuration:**

The indexes are defined in `backend/firestore.indexes.json`:

```json
{
  "indexes": [
    {
      "collectionGroup": "patients",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "user_id", "order": "ASCENDING"},
        {"fieldPath": "last_name_lower", "order": "ASCENDING"},
        {"fieldPath": "first_name_lower", "order": "ASCENDING"},
        {"fieldPath": "__name__", "order": "ASCENDING"}
      ]
    },
    {
      "collectionGroup": "patients",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "user_id", "order": "ASCENDING"},
        {"fieldPath": "first_name_lower", "order": "ASCENDING"},
        {"fieldPath": "last_name_lower", "order": "ASCENDING"},
        {"fieldPath": "__name__", "order": "ASCENDING"}
      ]
    },
    {
      "collectionGroup": "therapy_sessions",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "user_id", "order": "ASCENDING"},
        {"fieldPath": "session_date", "order": "DESCENDING"},
        {"fieldPath": "__name__", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "therapy_sessions",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "patient_id", "order": "ASCENDING"},
        {"fieldPath": "user_id", "order": "ASCENDING"},
        {"fieldPath": "session_date", "order": "DESCENDING"},
        {"fieldPath": "__name__", "order": "DESCENDING"}
      ]
    },
    {
      "collectionGroup": "therapy_sessions",
      "queryScope": "COLLECTION",
      "fields": [
        {"fieldPath": "patient_id", "order": "ASCENDING"},
        {"fieldPath": "session_number", "order": "DESCENDING"},
        {"fieldPath": "__name__", "order": "DESCENDING"}
      ]
    }
  ]
}
```

**Notes:**
- Index creation is asynchronous and takes 5-10 minutes
- If you skip this step, Firestore will auto-create indexes on first query (slower)
- Check index status: https://console.cloud.google.com/firestore/indexes

**Verify Index Deployment:**

```bash
# List all composite indexes
gcloud firestore indexes composite list --project=$PROJECT_ID

# Expected output should include indexes for:
# - patients (user_id + last_name_lower + first_name_lower)
# - patients (user_id + first_name_lower + last_name_lower)
# - therapy_sessions (user_id + session_date)
# - therapy_sessions (patient_id + user_id + session_date)
# - therapy_sessions (patient_id + session_number)

# Check that all indexes show "state: READY" (not "state: CREATING")
```

#### Step 4: Set Up Firebase Authentication

```bash
# Initialize Firebase
firebase init auth

# Configure Google OAuth provider in Firebase Console:
# 1. Go to: https://console.firebase.google.com/project/$PROJECT_ID/authentication/providers
# 2. Enable "Google" provider
# 3. Add authorized domain: your-cloud-run-url.run.app
```

#### Step 5: Create Service Accounts

```bash
# Backend service account
gcloud iam service-accounts create therapy-backend \
  --display-name="Pablo Backend"

export BACKEND_SA="therapy-backend@$PROJECT_ID.iam.gserviceaccount.com"

# Grant permissions
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$BACKEND_SA" \
  --role="roles/datastore.user"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$BACKEND_SA" \
  --role="roles/aiplatform.user"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$BACKEND_SA" \
  --role="roles/secretmanager.secretAccessor"
```

#### Step 6: Build Docker Images

```bash
# Build backend
gcloud builds submit ./backend \
  --tag="us-docker.pkg.dev/$PROJECT_ID/therapy/backend:latest" \
  --dockerfile=backend/Dockerfile.production

# Build frontend
gcloud builds submit ./frontend \
  --tag="us-docker.pkg.dev/$PROJECT_ID/therapy/frontend:latest" \
  --dockerfile=frontend/Dockerfile.production
```

#### Step 7: Create Secrets

```bash
# Generate JWT secret
export JWT_SECRET=$(openssl rand -base64 32)

echo -n "$JWT_SECRET" | gcloud secrets create jwt-secret-key \
  --data-file=- \
  --replication-policy="automatic"

# Grant backend access to secrets
gcloud secrets add-iam-policy-binding jwt-secret-key \
  --member="serviceAccount:$BACKEND_SA" \
  --role="roles/secretmanager.secretAccessor"
```

#### Step 8: Deploy Backend

```bash
gcloud run deploy therapy-backend \
  --image="us-docker.pkg.dev/$PROJECT_ID/therapy/backend:latest" \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --service-account=$BACKEND_SA \
  --memory=1Gi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=10 \
  --set-env-vars="GCP_PROJECT_ID=$PROJECT_ID" \
  --set-env-vars="ENVIRONMENT=production" \
  --set-env-vars="REQUIRE_BAA=false" \
  --set-env-vars="ENFORCE_HTTPS=true" \
  --set-secrets="JWT_SECRET_KEY=jwt-secret-key:latest"

# Get backend URL
export BACKEND_URL=$(gcloud run services describe therapy-backend \
  --region=$REGION \
  --format='value(status.url)')

echo "Backend URL: $BACKEND_URL"
```

#### Step 9: Deploy Frontend

```bash
gcloud run deploy therapy-frontend \
  --image="us-docker.pkg.dev/$PROJECT_ID/therapy/frontend:latest" \
  --region=$REGION \
  --platform=managed \
  --allow-unauthenticated \
  --memory=512Mi \
  --cpu=1 \
  --min-instances=0 \
  --max-instances=5 \
  --set-env-vars="NEXT_PUBLIC_API_URL=$BACKEND_URL" \
  --set-env-vars="NEXT_PUBLIC_FIREBASE_PROJECT_ID=$PROJECT_ID"

# Get frontend URL
export FRONTEND_URL=$(gcloud run services describe therapy-frontend \
  --region=$REGION \
  --format='value(status.url)')

echo "Frontend URL: $FRONTEND_URL"
```

#### Step 10: Create Admin User

```bash
# Use Firebase Admin SDK or Firebase Console
# 1. Go to: https://console.firebase.google.com/project/$PROJECT_ID/authentication/users
# 2. Click "Add User"
# 3. Enter your email
# 4. Note the UID

# Or use Firebase CLI:
firebase auth:import admin-user.json --project=$PROJECT_ID
```

#### Step 11: Configure Custom Domain (Optional)

```bash
# Map custom domain to frontend
gcloud run domain-mappings create \
  --service=therapy-frontend \
  --domain=app.yourdomain.com \
  --region=$REGION

# Map API subdomain to backend
gcloud run domain-mappings create \
  --service=therapy-backend \
  --domain=api.yourdomain.com \
  --region=$REGION

# Follow DNS instructions provided by the command
```

#### Step 12: Verify Deployment

```bash
# Test backend health
curl "$BACKEND_URL/health"

# Test frontend
open "$FRONTEND_URL"

# Check logs
gcloud run services logs read therapy-backend --region=$REGION --limit=20
gcloud run services logs read therapy-frontend --region=$REGION --limit=20
```

### Post-Deployment Configuration

#### Enable HIPAA Audit Logging

```bash
# Configure audit logs (already enabled in Step 3, but verify)
gcloud logging read 'protoPayload.serviceName="firestore.googleapis.com"' --limit=5

# Set up log retention (6 years for HIPAA)
gcloud logging buckets update _Default \
  --location=global \
  --retention-days=2190  # 6 years
```

#### Configure Backups

```bash
# Firestore automatic backups
gcloud firestore backups schedules create \
  --database='(default)' \
  --recurrence=daily \
  --retention=7d

# Export to Cloud Storage for long-term retention
export BACKUP_BUCKET="gs://$PROJECT_ID-backups"
gsutil mb -p $PROJECT_ID -c STANDARD -l $REGION $BACKUP_BUCKET

# Set lifecycle policy (delete after 7 years)
cat > lifecycle.json <<EOF
{
  "lifecycle": {
    "rule": [
      {
        "action": {"type": "Delete"},
        "condition": {"age": 2555}
      }
    ]
  }
}
EOF

gsutil lifecycle set lifecycle.json $BACKUP_BUCKET
```

#### Set Up Monitoring

```bash
# Create uptime check
gcloud monitoring uptime create therapy-frontend-health \
  --resource-type=uptime-url \
  --host="$FRONTEND_URL" \
  --display-name="Therapy Frontend Health"

# Create alert policy for downtime
gcloud alpha monitoring policies create \
  --notification-channels=YOUR_NOTIFICATION_CHANNEL_ID \
  --display-name="Pablo Downtime Alert" \
  --condition-display-name="Frontend Down" \
  --condition-threshold-value=1 \
  --condition-threshold-duration=300s
```

---

## HIPAA Compliance

### Compliance Checklist

- [ ] **TLS/HTTPS Enforcement**
  - All Cloud Run services use HTTPS (enforced by default)
  - HSTS headers configured (`Strict-Transport-Security`)
  - SSL Labs score: A or higher

- [ ] **Data Encryption**
  - At rest: Firestore encrypted by default (Google-managed keys)
  - In transit: TLS 1.2+ for all connections
  - Backups: Cloud Storage with encryption

- [ ] **Access Controls**
  - Firebase Authentication for user access
  - IAM roles for service accounts (principle of least privilege)
  - Multi-factor authentication (MFA) required for admin users

- [ ] **Audit Logging**
  - Cloud Audit Logs enabled for all Firestore operations
  - Log retention: 6 years minimum (HIPAA requirement)
  - Automated monitoring for unauthorized access

- [ ] **Data Retention & Deletion**
  - Firestore backups: 7 days rolling (operational)
  - Cloud Storage exports: 7 years (compliance)
  - Patient data deletion API implemented

- [ ] **Business Associate Agreement (BAA)**
  - Individual deployment: Not required (therapist is self-hosting)
  - SaaS deployment: BAA signed with Google Cloud
  - BAA tracking: Implemented in application (`REQUIRE_BAA` flag)

- [ ] **Incident Response**
  - Security alerting configured (unauthorized access, unusual patterns)
  - Incident response plan documented
  - Breach notification process defined

### HIPAA Verification Steps

After deployment, verify HIPAA compliance:

```bash
# 1. Test TLS configuration
curl -I https://your-app.run.app | grep -i strict-transport

# SSL Labs test (manual)
# Go to: https://www.ssllabs.com/ssltest/analyze.html?d=your-app.run.app

# 2. Verify audit logs are enabled
gcloud logging read 'protoPayload.serviceName="firestore.googleapis.com"' \
  --limit=10 \
  --format=json

# 3. Check log retention policy
gcloud logging buckets describe _Default --location=global

# 4. Verify encryption at rest
gcloud firestore databases describe --database='(default)' | grep encryptionConfig

# 5. Test HTTPS enforcement (HTTP should fail)
curl -I http://your-app.run.app
# Expected: 301 redirect to HTTPS or connection refused

# 6. Verify service account permissions (principle of least privilege)
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:serviceAccount:therapy-backend*"
```

### BAA Management (SaaS Only)

For SaaS deployments, track which therapists have signed BAAs:

```python
# In backend/app/models.py
class Therapist(BaseModel):
    therapist_id: str
    email: str
    baa_signed: bool
    baa_signed_date: datetime | None
    baa_document_url: str | None

# In backend/app/middleware.py
async def verify_baa(request: Request, call_next):
    if settings.REQUIRE_BAA:
        therapist_id = get_therapist_from_request(request)
        therapist = await get_therapist(therapist_id)

        if not therapist.baa_signed:
            return JSONResponse(
                status_code=403,
                content={"error": "BAA signature required before use"}
            )

    return await call_next(request)
```

---

## Troubleshooting

### Common Issues

#### Issue: "Permission denied" when deploying

**Symptom**: `gcloud run deploy` fails with permission error

**Solution**:
```bash
# Verify you have the correct roles
gcloud projects get-iam-policy $PROJECT_ID \
  --flatten="bindings[].members" \
  --filter="bindings.members:user:YOUR_EMAIL"

# Add required roles
gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="user:YOUR_EMAIL" \
  --role="roles/run.admin"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="user:YOUR_EMAIL" \
  --role="roles/iam.serviceAccountUser"
```

#### Issue: Firestore "PERMISSION_DENIED" errors

**Symptom**: Backend logs show Firestore permission errors

**Solution**:
```bash
# Check service account has Firestore access
export BACKEND_SA="therapy-backend@$PROJECT_ID.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding $PROJECT_ID \
  --member="serviceAccount:$BACKEND_SA" \
  --role="roles/datastore.user"

# Redeploy service to pick up new permissions
gcloud run services update therapy-backend --region=$REGION
```

#### Issue: Firestore "index required" errors

**Symptom**: API requests fail with error: "The query requires an index"

**Solution**:
```bash
# Check if indexes are deployed
gcloud firestore indexes composite list --project=$PROJECT_ID

# Deploy indexes if missing
firebase deploy --only firestore:indexes --project=$PROJECT_ID

# Check deployment status
# Visit: https://console.cloud.google.com/firestore/indexes?project=$PROJECT_ID

# Wait for indexes to build (5-10 minutes)
# Status will change from "Building" to "Enabled"
```

**Common causes:**
- Indexes weren't deployed during setup
- New query patterns added (requires new indexes)
- Index deployment failed silently

**To add a new index:**
1. Firestore will provide the exact index definition in the error message
2. Add it to `backend/firestore.indexes.json`
3. Run `firebase deploy --only firestore:indexes`

#### Issue: Frontend can't reach backend API

**Symptom**: Network errors in browser console when calling API

**Solution**:
```bash
# Verify CORS is configured correctly
curl -H "Origin: $FRONTEND_URL" \
  -H "Access-Control-Request-Method: GET" \
  -X OPTIONS \
  "$BACKEND_URL/health" -v

# Check backend logs for CORS errors
gcloud run services logs read therapy-backend --region=$REGION --limit=50

# Update backend CORS_ORIGINS setting
gcloud run services update therapy-backend \
  --region=$REGION \
  --set-env-vars="CORS_ORIGINS=$FRONTEND_URL"
```

#### Issue: "Service Unavailable" (503 errors)

**Symptom**: Intermittent 503 errors, especially on first request

**Solution**:
```bash
# Increase minimum instances to avoid cold starts
gcloud run services update therapy-backend \
  --region=$REGION \
  --min-instances=1

# Check memory/CPU limits (may need to increase)
gcloud run services update therapy-backend \
  --region=$REGION \
  --memory=4Gi \
  --cpu=2
```

#### Issue: High costs / unexpected billing

**Symptom**: Monthly bill exceeds estimates

**Solution**:
```bash
# Check resource usage
gcloud monitoring dashboards list
gcloud monitoring time-series-query 'fetch cloud_run_revision::run.googleapis.com/container/memory/utilizations'

# Common causes:
# 1. Min instances too high (set to 0 for dev)
# 2. Large memory allocation (reduce if not needed)
# 3. Too many Vertex AI API calls (optimize SOAP generation)
# 4. Excessive logging (adjust log level to WARNING in production)

# Set budget alert
gcloud billing budgets create \
  --billing-account=$BILLING_ACCOUNT_ID \
  --display-name="Pablo Budget" \
  --budget-amount=100USD \
  --threshold-rule=percent=50 \
  --threshold-rule=percent=90
```

### Debugging Tips

#### View Real-Time Logs

```bash
# Backend logs
gcloud run services logs tail therapy-backend --region=$REGION

# Frontend logs
gcloud run services logs tail therapy-frontend --region=$REGION

# Filter by severity
gcloud run services logs read therapy-backend \
  --region=$REGION \
  --log-filter='severity>=ERROR' \
  --limit=50
```

#### Test Backend Directly

```bash
# Health check
curl "$BACKEND_URL/health"

# Get OpenAPI docs
curl "$BACKEND_URL/docs"

# Test authentication (get JWT from browser devtools)
export JWT_TOKEN="eyJhbGc..."
curl -H "Authorization: Bearer $JWT_TOKEN" \
  "$BACKEND_URL/api/v1/patients"
```

#### Check Firestore Data

```bash
# Install Firebase CLI
npm install -g firebase-tools

# Login
firebase login

# Query Firestore
firebase firestore:get patients --project=$PROJECT_ID

# Count documents
gcloud firestore indexes composite describe INDEX_NAME \
  --database='(default)'
```

---

## Monitoring & Maintenance

### Health Monitoring

#### Uptime Checks

```bash
# Create uptime check for backend
gcloud monitoring uptime create therapy-backend-health \
  --resource-type=uptime-url \
  --host="$BACKEND_URL" \
  --path="/health" \
  --check-interval=5m \
  --display-name="Therapy Backend Health"

# Create uptime check for frontend
gcloud monitoring uptime create therapy-frontend-health \
  --resource-type=uptime-url \
  --host="$FRONTEND_URL" \
  --check-interval=5m \
  --display-name="Therapy Frontend Health"
```

#### Alert Policies

```bash
# Create notification channel (email)
gcloud alpha monitoring channels create \
  --type=email \
  --display-name="Admin Alert Email" \
  --channel-labels=email_address=admin@example.com

export CHANNEL_ID="..."  # From output above

# Alert on high error rate
gcloud alpha monitoring policies create \
  --notification-channels=$CHANNEL_ID \
  --display-name="High Error Rate Alert" \
  --condition-display-name="Error Rate > 5%" \
  --condition-threshold-value=0.05 \
  --condition-threshold-duration=300s
```

### Performance Monitoring

#### Cloud Run Metrics

Key metrics to monitor:
- **Request count**: Total API requests
- **Request latency**: p50, p95, p99 response times
- **Container CPU utilization**: Should stay < 80%
- **Container memory utilization**: Should stay < 80%
- **Billable instance time**: Correlates to Cloud Run costs

View in Cloud Console: https://console.cloud.google.com/run

#### Firestore Metrics

Key metrics to monitor:
- **Read/write operations**: Track usage patterns
- **Storage size**: Monitor data growth
- **Index size**: May indicate missing composite indexes
- **Read/write costs**: Major cost driver

```bash
# View Firestore usage
gcloud firestore operations list --database='(default)'
```

### Maintenance Tasks

#### Regular Updates

```bash
# Update backend image (after new release)
gcloud builds submit ./backend \
  --tag="us-docker.pkg.dev/$PROJECT_ID/therapy/backend:v1.2.0"

gcloud run services update therapy-backend \
  --region=$REGION \
  --image="us-docker.pkg.dev/$PROJECT_ID/therapy/backend:v1.2.0"

# Update frontend image
gcloud builds submit ./frontend \
  --tag="us-docker.pkg.dev/$PROJECT_ID/therapy/frontend:v1.2.0"

gcloud run services update therapy-frontend \
  --region=$REGION \
  --image="us-docker.pkg.dev/$PROJECT_ID/therapy/frontend:v1.2.0"
```

#### Backup Verification

```bash
# List Firestore backups
gcloud firestore backups list --location=nam5

# Test restore (to test project)
gcloud firestore import gs://$BACKUP_BUCKET/backup-20260118 \
  --database='(default)'
```

#### Security Audits

```bash
# Review IAM permissions (quarterly)
gcloud projects get-iam-policy $PROJECT_ID > iam-policy-$(date +%Y%m%d).json

# Review audit logs for suspicious activity
gcloud logging read 'protoPayload.serviceName="firestore.googleapis.com" AND severity>=WARNING' \
  --limit=100 \
  --format=json

# Check for outdated dependencies (in local dev)
cd backend && poetry update --dry-run
cd frontend && npm outdated
```

#### Cost Optimization

```bash
# View cost breakdown
gcloud billing accounts get-iam-policy $BILLING_ACCOUNT_ID

# Identify top cost drivers
gcloud monitoring time-series list \
  --filter='metric.type="run.googleapis.com/container/billable_instance_time"'

# Optimize:
# 1. Reduce min-instances to 0 for low-traffic services
# 2. Lower memory allocation if utilization < 50%
# 3. Use Firestore composite indexes to reduce read costs
# 4. Compress audit logs before long-term storage
# 5. Consider Cloud CDN for static frontend assets
```

---

## Next Steps

After successful deployment:

1. **Test End-to-End**
   - Login with Google OAuth
   - Create a test patient
   - Upload a sample transcript
   - Generate a SOAP note
   - Export patient data (PDF/JSON)
   - Verify audit logs capture all actions

2. **Configure Monitoring**
   - Set up uptime checks
   - Create alert policies for downtime and errors
   - Enable cost alerts

3. **Documentation for Users**
   - Provide therapist with login URL
   - Document how to create patients and sessions
   - Explain HIPAA compliance features (audit logs, encryption)

4. **Pilot Testing** (3-5 therapists, 2-4 weeks)
   - Gather feedback on SOAP note quality
   - Monitor costs and performance
   - Identify bugs and UX issues

5. **Iterate and Scale**
   - Fix issues found during pilot
   - Consider SaaS deployment for broader rollout
   - Implement additional features based on feedback

---

## Support

### Getting Help

- **Documentation**: https://github.com/pablo-health/pablo/tree/main/docs
- **Issues**: https://github.com/pablo-health/pablo/issues
- **GCP Support**: https://cloud.google.com/support
- **HIPAA Compliance**: https://cloud.google.com/security/compliance/hipaa

### Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines.

### License

This project is licensed under the AGPL-3.0 License - see [LICENSE](../LICENSE) for details.

---

**Last Updated**: 2026-03-19
**Version**: 1.0.0
