# Google Cloud Platform Deployment Guide

Pablo is designed to be self-hosted on Google Cloud Platform. The recommended path is to run `./setup-solo.sh` — it walks through everything interactively. This guide covers what the script provisions, how to deploy manually if the script fails, and what you own after the install.

## Table of Contents

- [Architecture](#architecture)
- [What setup-solo.sh Provisions](#what-setup-solosh-provisions)
- [Prerequisites](#prerequisites)
- [Automated Install](#automated-install)
- [Manual Deployment](#manual-deployment)
- [Post-Install Responsibilities](#post-install-responsibilities)
- [Updates](#updates)
- [Troubleshooting](#troubleshooting)
- [Monitoring](#monitoring)

---

## Architecture

```
Your GCP Project
├── Cloud Run
│   ├── pablo-backend   (FastAPI)
│   └── pablo-frontend  (Next.js)
├── Cloud SQL
│   └── pablo           (PostgreSQL 16, Enterprise edition)
├── Identity Platform
│   ├── Email/password + Google OAuth
│   └── Mandatory TOTP MFA (NIST 800-63B password policy)
├── Secret Manager
│   ├── DATABASE_URL
│   ├── JWT_SECRET_KEY
│   └── AI API keys (if using Anthropic)
├── Artifact Registry
│   └── Mirror of pre-built images from ghcr.io/pablo-health
└── Cloud Audit Logs
    └── HIPAA compliance tracking
```

**Characteristics:**
- **Data sovereignty**: You own the GCP project and all data.
- **Isolation**: Single-tenant. No shared infrastructure with other deployments.
- **HIPAA**: You are the covered entity. See [SELF_HOSTING_HIPAA_GUIDE.md](SELF_HOSTING_HIPAA_GUIDE.md).

---

## What setup-solo.sh Provisions

The script is idempotent — rerunning it skips anything that already exists. In order, it:

1. Verifies / selects a GCP billing account.
2. Creates (or reuses) a GCP project and links billing.
3. Enables required APIs: Cloud Run, Cloud SQL, Identity Platform, Secret Manager, Artifact Registry, Cloud Audit Logs, Vertex AI.
4. Creates an Artifact Registry repo as the Cloud Run deploy target.
5. Creates a Cloud SQL PostgreSQL 16 instance (`db-f1-micro`, Enterprise edition) and the `pablo` database.
6. Configures Identity Platform with mandatory TOTP MFA and a 15+ character password policy.
7. Stores `DATABASE_URL`, `JWT_SECRET_KEY`, and Google OAuth credentials in Secret Manager.
8. Mirrors pre-built backend and frontend images from `ghcr.io/pablo-health` into your Artifact Registry via `gcloud artifacts docker images copy` (no local Docker or Cloud Build required).
9. Deploys both services to Cloud Run, wired to the database and Identity Platform.
10. Prints the frontend URL and next steps.

**Expected runtime:** 10–15 minutes, most of it waiting on Cloud SQL instance creation.

**Pin a specific release** by setting `PABLO_VERSION` before invoking the script:

```bash
export PABLO_VERSION=v0.1.0
./setup-solo.sh
```

Defaults to `latest`.

---

## Prerequisites

- Google Cloud account with billing enabled.
- `gcloud` CLI installed and authenticated ([install guide](https://cloud.google.com/sdk/docs/install)), or use Cloud Shell.
- Permission to create projects and link billing, or an existing project where you have Owner role.

---

## Automated Install

From Cloud Shell or a local terminal:

```bash
git clone https://github.com/pablo-health/pablo.git
cd pablo
./setup-solo.sh
```

The script is interactive — it will prompt for project ID, region, AI model choice, and the admin account to seed.

---

## Manual Deployment

If the script fails partway through, you can continue from the failing step manually. The most common sticking points:

### Cloud SQL: "Invalid Tier for ENTERPRISE_PLUS Edition"

Newer GCP projects may default to the Enterprise Plus edition, which does not support `db-f1-micro`. Fix:

```bash
gcloud sql instances create pablo \
  --database-version=POSTGRES_16 \
  --edition=ENTERPRISE \
  --tier=db-f1-micro \
  --region=us-central1 \
  --storage-size=10 \
  --storage-auto-increase \
  --assign-ip
```

Or in the console: **Create Instance → PostgreSQL → Edition: Enterprise** (not Enterprise Plus).

### Identity Platform not enabled

Identity Platform requires a one-time console opt-in:

1. Visit `https://console.cloud.google.com/customer-identity?project=$PROJECT_ID`.
2. Click **Enable Identity Platform**.
3. Rerun `./setup-solo.sh`.

### Secrets not visible to Cloud Run

The compute service account needs `roles/secretmanager.secretAccessor`. The script grants this, but if you edited secrets manually:

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format="value(projectNumber)")
gcloud secrets add-iam-policy-binding DATABASE_URL \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Post-Install Responsibilities

Self-hosting means you take on the HIPAA responsibilities that a managed service would cover. Minimum items:

- **Sign the Google Cloud BAA.** Console → Settings → Compliance.
- **Enable Cloud SQL automated backups.** Console → SQL → `pablo` → Backups.
- **Enable point-in-time recovery** on Cloud SQL.
- **Enable Data Access audit logs** for Cloud SQL (`DATA_READ` and `DATA_WRITE`) in IAM → Audit Logs.
- **Set log retention to 6+ years** in Cloud Logging.
- **Enroll admin accounts in MFA** on first login.

Full checklist: [SELF_HOSTING_HIPAA_GUIDE.md](SELF_HOSTING_HIPAA_GUIDE.md).

---

## Updates

```bash
git pull
./redeploy.sh                 # pulls :latest from ghcr.io and redeploys
PABLO_VERSION=v0.2.0 ./redeploy.sh   # pin to a specific release
./redeploy.sh backend         # redeploy backend only
```

`redeploy.sh` mirrors the requested image tag from `ghcr.io/pablo-health` into your Artifact Registry and rolls Cloud Run to the new revision. It does not touch the database, secrets, or Identity Platform config.

---

## Troubleshooting

### Backend logs

```bash
gcloud run services logs read pablo-backend --region=us-central1 --limit=100
```

### Database connection

Cloud Run connects to Cloud SQL via the Cloud SQL Python Connector using IAM auth. If you see connection errors, confirm:

- The Cloud Run service account has `roles/cloudsql.client`.
- The `DATABASE_URL` secret points to the instance connection name (`PROJECT:REGION:INSTANCE`), not a raw IP.

### Identity Platform login loop

If users see a redirect loop on sign-in:

- Confirm the frontend URL is in the **Authorized domains** list in Identity Platform.
- Confirm the backend has the correct `NEXT_PUBLIC_FIREBASE_*` env vars baked into the frontend image.

---

## Monitoring

- **Cloud Run metrics**: request count, latency, error rate per service.
- **Cloud SQL dashboard**: CPU, memory, disk, connection count.
- **Uptime checks**: Create one against the backend `/health` endpoint.
- **Budget alerts**: Console → Billing → Budgets & alerts.

For deeper observability, enable Cloud Trace and Cloud Profiler on Cloud Run — both are free at low volume.
