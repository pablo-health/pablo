# Epic / MyChart patient-data puller

A **standalone** CLI that pulls a patient's records out of Epic via
**SMART on FHIR** and writes them to disk as FHIR JSON. It runs the
"standalone patient launch" flow: you (the patient) sign in to MyChart,
authorize the app, and the tool downloads your record. Nothing is sent
into the Pablo backend — this is the proof-of-concept import path that
deeper Pablo integration will build on.

> **Why SMART on FHIR and not screen-scraping?** Epic does not allow
> programmatic MyChart logins or HTML scraping. The sanctioned route is
> the FHIR API behind OAuth2/SMART, which every modern Epic org exposes.

## 1. Register a (sandbox) app — one time

The sandbox needs a client id tied to *your* app registration. There is
no shared public client id.

1. Go to <https://fhir.epic.com> and sign in (free developer account).
2. **Build Apps → Create**. Choose:
   - **Application Audience:** *Patients* (this is what enables
     standalone patient launch).
   - **FHIR version:** *R4*.
   - **OAuth 2.0:** enable; this is a **public** client (no secret) using
     **PKCE**.
   - **Redirect URI:** `http://127.0.0.1:8765/callback`
     (must match `--port` / `EPIC_REDIRECT_PORT` exactly).
   - **Incoming APIs / scopes:** select the patient-read resources
     (Patient, Condition, MedicationRequest, AllergyIntolerance,
     Observation, Immunization, Procedure, DiagnosticReport,
     DocumentReference, Encounter) plus `openid`, `fhirUser`,
     `offline_access`.
3. Save. Copy the **Non-Production Client ID**.

Sandbox approval for non-production client ids is immediate.

## 2. Sandbox test patients

When the tool opens MyChart, sign in with a sandbox test login (password
is `epicfhir11` for all of them):

| Patient        | Username      |
| -------------- | ------------- |
| Camila Lopez   | `fhircamila`  |
| Derrick Lin    | `fhirderrick` |
| Desiree Powell | `fhirdesiree` |
| Warren McGinnis| `fhirwarren`  |

(Epic publishes the full list in the sandbox docs.)

## 3. Run it

First confirm the endpoint is reachable (no login, no client id needed):

```bash
cd backend
poetry run python -m integrations.epic --check
```

Expected output (verified against the live sandbox):

```
Checking Epic FHIR endpoint: https://fhir.epic.com/interconnect-fhir-oauth/api/FHIR/R4
  authorize endpoint: https://fhir.epic.com/interconnect-fhir-oauth/oauth2/authorize
  token endpoint:     https://fhir.epic.com/interconnect-fhir-oauth/oauth2/token
  FHIR version:       4.0.1
```

Then run the real pull:

```bash
export EPIC_CLIENT_ID=your-non-production-client-id

# Run from the backend/ directory so `integrations` is importable
# (matches how the rest of the backend is invoked, e.g. PYTHONPATH=backend).
cd backend
poetry run python -m integrations.epic
```

A browser opens to MyChart; sign in as a test patient and approve. The
tool captures the redirect on `127.0.0.1:8765`, exchanges the code, and
writes a timestamped run under `epic_export/`:

```
epic_export/20260621T154500Z/
  Patient.json
  Condition.json
  MedicationRequest.json
  Observation_laboratory.json
  ...
  _export_metadata.json
```

Each search file is a FHIR `searchset` Bundle (all pages concatenated);
`_export_metadata.json` records the patient id, timestamp, and per-type
counts.

### Useful flags

| Flag / env var                       | Purpose                                            |
| ------------------------------------ | -------------------------------------------------- |
| `--client-id` / `EPIC_CLIENT_ID`     | Your registered app's client id (**required**).    |
| `--fhir-base-url` / `EPIC_FHIR_BASE_URL` | Point at a real Epic org instead of the sandbox.   |
| `--output-dir` / `EPIC_OUTPUT_DIR`   | Where export runs are written.                     |
| `--port` / `EPIC_REDIRECT_PORT`      | Loopback callback port (match the registered URI). |
| `--no-browser`                       | Print the auth URL instead of opening a browser.   |

## Where imported data lands (retention sinks)

Mapping (`mappers`) is shared; *where the data is kept* is a pluggable
`ImportSink`, because the two use cases have different legal/retention models:

- **`TenantSink`** (`tenant_sink.py`) — clinician / prescriber case. Pablo is
  a Business Associate; data lands in the practice tenant schema via the
  access-scoped repositories (inherits RLS + soft-delete/purge), provenance-
  tagged. The triggering route owns the audit entry.
- **`PatientOwnedSink`** (`patient_store.py`) — patient-support case. The
  record is **encrypted at rest** (`vault.py`, Fernet/AES), carries a **TTL**
  (default 30 days), and is removable with a one-call **`purge()`**. PHR /
  FTC-Health-Breach-Notification-Rule posture — deliberately *not* in a
  clinician's schema. Key custody is pluggable (`KeyProvider`):
  - **`LocalKeyProvider`** — zero-knowledge. Key is patient-held (generated or
    passphrase-derived via scrypt); Pablo can't decrypt, so no server-side AI.
    Nothing secret is persisted.
  - **`CmekKeyProvider`** (`cmek.py`) — Cloud KMS envelope encryption. A
    per-record DEK is wrapped by a per-patient KMS key; Pablo (with IAM on the
    key) can decrypt for server-side AI, and destroying the KMS key
    **crypto-shreds** every record under it. One KMS call per record (cost
    tracks record count, not size). Needs `google-cloud-kms` in production.

Sensitivity filtering (42 CFR Part 2 / DS4P) is applied before either sink,
so specially-protected data only lands with a deliberate opt-in.

## Pointing at a real MyChart org

Once you have a **production** client id approved for a specific Epic
organization, set `EPIC_CLIENT_ID` to it and `EPIC_FHIR_BASE_URL` to that
org's FHIR R4 base (from their Epic endpoint directory). The flow is
identical; the patient signs in with their real MyChart credentials.

## Two auth modes

The token-acquisition strategy is pluggable (`TokenProvider` → `AccessGrant`);
everything downstream (FHIR client, exporter) is identical regardless of mode.

| Mode | `--auth-mode` | Who authorizes | When Pablo uses it |
| ---- | ------------- | -------------- | ------------------ |
| **Patient launch** (default) | `patient` | the patient, via MyChart login (auth-code + PKCE) | patient-mediated import; works across orgs with no B2B deal |
| **Backend Services** | `backend` | the org, once (signed-JWT client-credentials, no browser) | headless server-side sync of a caseload, after the org onboards the app |

### Backend Services (headless)

No browser, no patient login. The app authenticates with a JWT signed by a
registered RSA key and gets a **system-level** token, so you name the patient
explicitly:

```bash
cd backend
poetry run python -m integrations.epic \
  --auth-mode backend \
  --client-id <backend-app-client-id> \
  --private-key /path/to/private_key.pem \
  --kid <public-jwk-kid> \
  --patient-id <fhir-patient-id>
```

Backend mode requires a **backend** app registration (system scopes +
the matching public JWK uploaded to Epic) — not the patient app above.
Env equivalents: `EPIC_BACKEND_PRIVATE_KEY_PATH`, `EPIC_BACKEND_KID`,
`EPIC_AUTH_MODE=backend`.

## Import levels (`--profile`)

A profile sets the **breadth/depth** of the pull and, crucially, the OAuth
**scopes requested** — so consent and import stay in lockstep (we never ask
for a scope the profile won't use):

| Profile | Resources | Use |
| ------- | --------- | --- |
| `minimal` | demographics + **active** problems + **active** meds | prescriber quick context |
| `clinical` | + allergies, labs, vitals, immunizations, encounters | fuller clinical picture |
| `full` (default) | + procedures, diagnostic reports, documents | everything the scopes allow |

```bash
poetry run python -m integrations.epic --profile minimal
```

### Sensitivity (42 CFR Part 2 / DS4P)

Specially-protected records — substance-use disorder (42 CFR Part 2),
psychiatry, HIV, sexual/domestic-violence — carry FHIR `meta.security`
labels. Ingestion **excludes these by default** (`exclude_sensitive=True`):
they are dropped before landing in any sink and counted as
`sensitive_skipped`. Opting in is a deliberate, consent-gated decision, not
a flag flip — importing Part 2 data without Part 2-compliant consent is a
violation, not a preference.

### Useful flags (both modes)

| Flag / env var                       | Purpose                                            |
| ------------------------------------ | -------------------------------------------------- |
| `--profile`                          | `minimal` / `clinical` / `full` (default `full`).  |
| `--auth-mode` / `EPIC_AUTH_MODE`     | `patient` (default) or `backend`.                  |
| `--client-id` / `EPIC_CLIENT_ID`     | Registered app's client id.                        |
| `--fhir-base-url` / `EPIC_FHIR_BASE_URL` | Point at a real Epic org instead of the sandbox.   |
| `--output-dir` / `EPIC_OUTPUT_DIR`   | Where export runs are written.                     |
| `--check`                            | Probe connectivity and exit (no login).            |
