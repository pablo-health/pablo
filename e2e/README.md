# E2E

Runs the Playwright suite against the deployed pablo environment on
`pablohealth-oss`. Wired into CI via two workflows:

- `.github/workflows/e2e-image-build.yml` — rebuilds the runner image
  whenever `e2e/**` changes on `main`.
- `.github/workflows/e2e.yml` — executes the `pablo-e2e` Cloud Run Job
  against the deployed services. Triggered post-deploy by `deploy.yml`
  and on-demand via `workflow_dispatch`.

The runner image is published to
`us-central1-docker.pkg.dev/pablohealth-oss/e2e/playwright:{sha,latest}`.

## Local development

```bash
cd e2e
npm ci
PLAYWRIGHT_BASE_URL=https://<pablo-frontend-cloud-run-url> npm test
```

Auth-gated specs additionally need `FIREBASE_API_KEY`, `TEST_PASSWORD`,
`PINNED_EMAIL`, and `TOTP_SECRET` — the values stored in Secret Manager
once the bootstrap steps below have been run.

## Bootstrap (one-time, per environment)

These steps need a human with admin access to `pablohealth-oss`. CI runs
the resulting Cloud Run Job — it does not create the secrets, the
Artifact Registry repo, or the GCS bucket.

### 1. Artifact Registry repo for the runner image

```bash
gcloud artifacts repositories create e2e \
  --project=pablohealth-oss --location=us-central1 \
  --repository-format=docker \
  --description="Playwright e2e runner image"
```

### 2. GCS bucket for artifacts

```bash
gcloud storage buckets create gs://pablohealth-oss-e2e-artifacts \
  --project=pablohealth-oss --location=us-central1 \
  --uniform-bucket-level-access
```

### 3. Service account for the Cloud Run Job

The job needs to: read its own secrets, write to the artifacts bucket,
call Firebase Identity Toolkit, and (for first-time provisioning) update
Firebase users via the admin endpoint.

```bash
gcloud iam service-accounts create pablo-e2e-runner \
  --project=pablohealth-oss \
  --display-name="pablo e2e Cloud Run Job runner"

# Allow it to read its e2e-oss-pinned-* secrets.
for SECRET in e2e-oss-pinned-email e2e-oss-pinned-password \
              e2e-oss-pinned-totp-secret e2e-oss-pinned-firebase-api-key; do
  gcloud secrets add-iam-policy-binding "$SECRET" \
    --project=pablohealth-oss \
    --member="serviceAccount:pablo-e2e-runner@pablohealth-oss.iam.gserviceaccount.com" \
    --role=roles/secretmanager.secretAccessor
done

# Allow it to upload artifacts.
gcloud storage buckets add-iam-policy-binding gs://pablohealth-oss-e2e-artifacts \
  --member="serviceAccount:pablo-e2e-runner@pablohealth-oss.iam.gserviceaccount.com" \
  --role=roles/storage.objectAdmin

# For one-time provisioning (provision-pinned-user.ts), also grant the
# Firebase admin role. After the pinned user is provisioned, this role
# is no longer needed on the runtime SA — the e2e runtime only does
# password sign-in via the gated API key, never admin-token operations.
gcloud projects add-iam-policy-binding pablohealth-oss \
  --member="serviceAccount:pablo-e2e-runner@pablohealth-oss.iam.gserviceaccount.com" \
  --role=roles/firebaseauth.admin
# Revoke this after provisioning:
#   gcloud projects remove-iam-policy-binding pablohealth-oss \
#     --member="serviceAccount:pablo-e2e-runner@pablohealth-oss.iam.gserviceaccount.com" \
#     --role=roles/firebaseauth.admin
```

### 4. Provision the pinned user

```bash
cd e2e
npm ci

# Get the deployed pablo-frontend URL.
BASE_URL=$(gcloud run services describe pablo-frontend \
  --project=pablohealth-oss --region=us-central1 \
  --format='value(status.url)')

# Pick a strong password, run the script. ADC must be a principal with
# roles/firebaseauth.admin on pablohealth-oss (your user, or a service
# account you've impersonated via `gcloud auth application-default
# login --impersonate-service-account=...`).
TEST_PASSWORD='<strong-random-password>' \
  PLAYWRIGHT_BASE_URL="$BASE_URL" \
  ./node_modules/.bin/tsx scripts/provision-pinned-user.ts > /tmp/pinned.json

# Inspect — the JSON has email/password/totpSecret/uid.
jq . /tmp/pinned.json
```

### 5. Stash credentials in Secret Manager

```bash
EMAIL=$(jq -r .email /tmp/pinned.json)
PASSWORD=$(jq -r .password /tmp/pinned.json)
TOTP=$(jq -r .totpSecret /tmp/pinned.json)
FIREBASE_API_KEY=$(curl -fsS "$BASE_URL/api/config" | jq -r .firebaseApiKey)

for PAIR in \
    "e2e-oss-pinned-email:$EMAIL" \
    "e2e-oss-pinned-password:$PASSWORD" \
    "e2e-oss-pinned-totp-secret:$TOTP" \
    "e2e-oss-pinned-firebase-api-key:$FIREBASE_API_KEY"; do
  NAME="${PAIR%%:*}"
  VALUE="${PAIR#*:}"
  printf %s "$VALUE" | gcloud secrets create "$NAME" \
    --project=pablohealth-oss --replication-policy=automatic --data-file=-
done

# Then shred the temp file.
shred -u /tmp/pinned.json
```

After that, the `e2e.yml` workflow's first run will create the
`pablo-e2e` Cloud Run Job (via `gcloud run jobs deploy` which is
create-or-update) wired to these secrets.

## Rotating the pinned-user password

Delete the user from Identity Platform, re-run the provisioning script,
overwrite the four secrets with `gcloud secrets versions add`. The
`pablo-e2e` Cloud Run Job reads `:latest` each run.

## Why pinned, not fresh-per-run

Every run reuses one pre-provisioned, BAA-accepted, MFA-enrolled user.
Cuts ~30s/run and removes the dependency on Firebase signUp being
healthy at test time — which would otherwise turn an auth outage into a
fake e2e failure.
