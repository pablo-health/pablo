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

## Spec coverage

The suite exercises the day-1 user-facing paths against the deployed
app. All auth-gated specs use the tiered fixtures in
`fixtures/auth.ts`; pick the cheapest fixture that gives you the state
you need (`enrolledUser` for API-only tests, `signedInPage` for UI).

| Spec | What it covers | Fixture |
| --- | --- | --- |
| `health-smoke` | API `/api/health` + frontend root | none |
| `spa-smoke` | SPA boots, config loads, unauthed → `/login` | none |
| `auth-fixtures` | `unenrolledUser` provisions a fresh, pre-MFA user | `unenrolledUser` |
| `onboarding` | full UI onboarding wizard → dashboard | `unenrolledUser` |
| `chart-render-smoke` | patient chart mounts with no console errors | `signedInPage` |
| `route-walk-smoke` | every top-level route renders cleanly | `signedInPage` |
| `patient-document-upload` | upload → byte round-trip → delete | `signedInPage` |
| `soap-from-transcript` | transcript upload drafts a SOAP note | `enrolledUser` |
| `manual-soap` | author/edit/finalize a standalone SOAP note | `enrolledUser` |
| `chat` | patient-context chat SSE + history + save-as-note | `enrolledUser` |

Two specs create a **fresh** user each run (`onboarding`,
`auth-fixtures`) via REST `signUp` + an admin `accounts:update` to mark
the email verified. That admin call needs `roles/firebaseauth.admin` on
the runtime SA — see the note in bootstrap step 3 about keeping (not
revoking) that role if you want these two specs in CI. The other specs
run in pinned mode and need only password sign-in via the gated API key.

`patient-document-upload` has an optional **layer 2** that inspects the
GCS object directly (size/MD5/content-type/ACL). It runs only when
`PATIENT_DOCS_BUCKET` is set and the runner has `storage.objectViewer`
on that bucket; otherwise the byte round-trip through the app (layer 1)
runs alone. Single-tenant deploys store objects under a fixed
`default/<category>/<uuid>` prefix.

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

# For one-time provisioning (provision-pinned-user.ts), grant the
# Firebase admin role.
gcloud projects add-iam-policy-binding pablohealth-oss \
  --member="serviceAccount:pablo-e2e-runner@pablohealth-oss.iam.gserviceaccount.com" \
  --role=roles/firebaseauth.admin
# Most specs run in pinned mode and need only password sign-in via the
# gated API key — for those you can revoke this role after provisioning:
#   gcloud projects remove-iam-policy-binding pablohealth-oss \
#     --member="serviceAccount:pablo-e2e-runner@pablohealth-oss.iam.gserviceaccount.com" \
#     --role=roles/firebaseauth.admin
# BUT the fresh-user specs (onboarding, auth-fixtures) call
# accounts:update to mark a freshly-signed-up user's email verified,
# which needs firebaseauth.admin at runtime. Keep the role if you want
# those two specs in CI; otherwise exclude them (e.g. pass an `args`
# filter to the e2e workflow).
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
