# Self-hosting Pablo under HIPAA

When you run Pablo yourself, you are the covered entity. Pablo Health is not
your business associate and does not see your records. This guide says what
the installer sets up for you, what it leaves to you, and how to tell the two
apart.

It describes the single-VM install (`./install.sh` from Cloud Shell, see
[deploy/vm/README.md](../deploy/vm/README.md)). It is written for a
clinician, or whoever looks after the practice's computers, and it names a
file for each claim so you can check it.

Two companions carry the detail:

- [Operations](self-hosting/operations.md): backups and restores, the
  scheduled jobs and what they produce, the audit sink, what to watch.
- [Settings and keys](self-hosting/settings-and-keys.md): every
  security-relevant setting, its effect, and how to rotate a key.

## What the installer builds

One Compute Engine VM in a Google Cloud project you own. Sources:
`install.sh`, `deploy/vm/lib/*.sh`, `deploy/vm/bootstrap.sh`,
`deploy/vm/compose.yaml`.

| Piece | What you get |
|---|---|
| Project | New or existing project, billing linked, the needed APIs turned on |
| Identity | A service account `pablo-vm` with the roles the app needs (Vertex AI, Identity Platform, logging, metrics, Secret Manager, Cloud Tasks) and access to its own buckets. No key file anywhere; the VM uses the metadata server |
| VM | Debian 12, `e2-small` by default. Shielded VM: Secure Boot, vTPM, integrity monitoring. OS Login on, project SSH keys blocked |
| Network | A static address. Firewall: ports 80 and 443 from the internet; port 22 only from Google's Identity-Aware Proxy range. No other inbound rule |
| HTTPS | Caddy with a Let's Encrypt certificate, renewed on its own |
| Database | Postgres 16 on its own disk (`pablo-data`), which survives deleting the VM. Listens only on loopback and the container network. The app's role is not a superuser. Data checksums on |
| Backups | pgBackRest to `gs://<project>-pablo-backups`: full weekly, differential daily, WAL archived continuously, four full backups kept. Daily disk snapshots, 14 kept, as a second line |
| Restore check | On the 2nd of each month the latest backup is restored into a scratch copy and queried |
| Sign-in | Identity Platform: email and password, 15-character minimum, authenticator-app MFA turned on. Google sign-in if you chose it. Only allowlisted emails may sign in; the installer allowlists yours |
| Secrets | Generated once into Secret Manager; the VM reads them at each update |
| Audit sink | A separate Archive bucket `<project>-hipaa-audit-6y` with a 2255-day retention policy, fed by two log sinks: Google's admin activity log and Pablo's audit events |
| Buckets | Session audio, patient documents, backups, compliance reports. Uniform access, public access prevented. The compliance bucket has a 7-year retention policy |
| Scheduled jobs | `/etc/cron.d/pablo`: a daily and a monthly audit log review, a weekly heartbeat, a quarterly attestation |
| Logs | Container output goes to Cloud Logging under the VM. The Ops Agent is installed |
| OS updates | `unattended-upgrades` for Debian packages. Pablo itself updates with `sudo pablo-update vX.Y.Z` |

Session audio goes to AssemblyAI only if you turned transcription on. Notes
are drafted by Gemini through Vertex AI in your project
(`AI_MODEL=google:gemini-3.1-pro-preview`, `VERTEX_REGION=global`).

## What the installer does not build

These are not oversights to hide; they are the lines where your work starts.

| Not done | Where it is covered |
|---|---|
| Alerting of any kind: no email when a backup fails, the disk fills or the site goes down | [Operations: what to watch](self-hosting/operations.md#what-to-watch) |
| Scheduled permanent deletion of records. Deleting a client hides the record; nothing removes it later | [Data retention and deletion](#data-retention-and-deletion) |
| Outbound email. Portal invitations and booking confirmations stay off until you add an SMTP provider | [BAA chain](#business-associate-agreements) |
| Customer-managed encryption keys. Disks and buckets use Google-managed keys | [Encryption](#encryption) |
| A penetration test. The weekly pentest in this repository runs against Pablo Health's hosted service, not your VM | — |
| Written policies, training records, a risk analysis, a contingency plan | [Shared responsibility](#shared-responsibility) |

## Shared responsibility

"Pablo" below means the code and the installer as shipped. "You" means the
practice. "Google" means what the Google Cloud BAA covers once you accept it.

| Safeguard | Pablo | You | Google |
|---|---|---|---|
| BAA with Google Cloud | Asks whether it is accepted before building anything | Accept it in the console, for the account that owns the project | — |
| BAA with AssemblyAI (transcription) | Asks; stores the key only if you say yes | Sign it before turning transcription on | — |
| Vertex AI terms | Sends notes through Vertex in your project | Confirm coverage with Google (see below) | Covered under the BAA per Google's list |
| Encryption in transit | Caddy TLS; the backend refuses plain HTTP outside development | Keep your own domain's DNS pointed at the VM | — |
| Encryption at rest | — | — | Google-managed keys on disks and buckets, by default |
| Unique user identity | One Identity Platform account per clinician; its id is on every audit row | Give each person their own account. Never share one | — |
| MFA | Authenticator app required; the backend refuses to start with it off outside development | Keep it that way | — |
| Password policy | 15 characters minimum, no composition rules | — | — |
| Automatic logoff | 15 minutes idle, enforced by the backend and fail-closed | — | — |
| Workforce access changes | Admin can disable a user (`PATCH /api/admin/users/{id}/disable`) | Remove leavers the same day, in Pablo and in Identity Platform | — |
| Audit of PHI access | Every PHI route writes to `audit_logs`; the table is append-only by trigger | Read it; the monthly review is yours to act on | — |
| Audit retention (6 years) | Mirror to the retention bucket, 2255 days, retention locked | Keep the VM's database too | — |
| Log review | Daily and monthly review reports to the compliance bucket | Read them; investigate HIGH findings | — |
| Backups | Continuous, with a monthly restore drill | Check the drill ran; know the restore steps | — |
| Disaster recovery | Data disk outlives the VM; images are tagged for rollback | Write down your recovery plan and test it yearly | — |
| Patching | Debian packages update themselves; releases are one command | Run `pablo-update` when a release ships; watch the repo | — |
| Monitoring | Jobs and backups write a one-line result to the log | Set up alerts, or check by hand on a schedule | — |
| Physical safeguards, data center | — | Your own devices: screen lock, disk encryption, disposal | Google's data centers, under the BAA |
| Risk analysis, policies, training, sanctions, incident response, breach notification | — | All yours. HIPAA requires them in writing | — |
| Business associate contracts | `docs/compliance/baa-manifest.yaml` lists every outbound destination | Keep your own copy, with dates | — |

Sources: `deploy/vm/lib/identity.sh` (password and MFA),
`backend/app/settings.py` (`require_mfa`, `idle_timeout_seconds`,
`idle_session_fail_open`), `backend/app/middleware/security.py` (HTTPS),
`backend/alembic/versions/b33a493310b6_*` and `a7e3f1b9c204_*` (append-only
triggers), `backend/app/routes/admin.py` (disable).

## Post-install checklist

Do these once the installer prints "Pablo is running".

1. **Sign in and enroll your authenticator app.** The first sign-in asks
   for it.
2. **Prove a restore works, today.** The drill also runs monthly.

   ```bash
   gcloud compute ssh pablo --zone=<zone> --tunnel-through-iap
   sudo pablo-backup info           # what the bucket holds
   sudo pablo-backup restore-drill  # restores the latest backup into a scratch copy
   ```

3. **Decide how you will find out when something breaks.** Nothing emails
   you by default. See [what to watch](self-hosting/operations.md#what-to-watch).
4. **Write your recovery steps down** where someone other than you can
   find them. [Operations](self-hosting/operations.md#restoring) has the
   outline.
5. **Record your BAAs**: Google Cloud (date accepted), AssemblyAI if used,
   your email provider if used. `docs/compliance/baa-manifest.yaml` is a
   starting list; fill in the dates for your copy.
6. **Confirm Vertex AI coverage with Google** for the global endpoint and
   the preview model (see below).
7. **Register the deployment** at [pablo.health/register](https://pablo.health/register)
   so you hear about security releases. Watching the GitHub repository's
   releases does the same.
8. **Put the paper in place**: risk analysis, policies, training, incident
   response. Pablo gives you evidence for some of them; it does not write
   them.

## Access control

- **Who can sign in.** `RESTRICT_SIGNUPS=true`: only allowlisted emails. The
  installer allowlists the admin email. An admin adds a colleague with
  `POST /api/admin/allowlist` (the app's admin area), or on the VM with
  `backend/bin/allow_email.py` the way `bootstrap.sh` does.
- **MFA.** Identity Platform has TOTP enabled (authenticator apps only, no
  SMS) and the backend requires the second factor on every token
  (`REQUIRE_MFA`, default on; the app refuses to boot with it off outside
  development). Passkeys are also available.
- **Idle logoff.** The backend ends a session after 15 minutes without a
  request (`idle_timeout_seconds`). The clock lives in Redis. If Redis
  cannot be reached, requests are refused rather than let through
  (`idle_session_fail_open=false`). The browser warns before the cutoff.
- **Leavers.** Disable the account in Pablo (admin, disable user) and in
  Identity Platform. Pablo's disable stops the app; Identity Platform's
  stops sign-in.
- **Server access.** SSH only through IAP with OS Login, so it is governed
  by IAM on the project. Every such session is in Google's admin activity
  log, which the audit sink keeps.
- **Database roles.** Row-level security limits each clinician to the
  clients they hold a grant on. Two narrow reads see past that (recognising
  a colleague's client from a calendar; not booking a shared session twice).
  They run as a role `pablo_practice_directory` that cannot log in, cannot
  bypass row-level security, and reads only id, name, date of birth and
  email. The migrations create it; on the VM the app's role has
  `CREATEROLE`, so nothing is needed by hand. If you ever run migrations as
  a different database user, see the note in
  `backend/app/db/practice_directory.py`.

## Encryption

| Where | How | Source |
|---|---|---|
| Browser to VM | TLS from Let's Encrypt, via Caddy. The backend answers 400 to plain HTTP and trusts `X-Forwarded-Proto` only from Caddy's fixed address (`TRUSTED_PROXY_IPS=172.30.0.10`) | `deploy/vm/Caddyfile`, `deploy/vm/compose.yaml`, `backend/app/middleware/security.py` |
| HSTS | Two years, subdomains, preload | `backend/app/settings.py` |
| VM to Google APIs | Google's TLS endpoints | — |
| Disks and buckets | Google-managed encryption at rest, Google's default. The installer does not configure customer-managed keys | `deploy/vm/lib/vm.sh`, `deploy/vm/lib/project.sh` (no `--kms-key`) |
| Database inside the VM | Postgres listens on loopback and the container network only; password auth with SCRAM | `deploy/vm/bootstrap.sh` |
| OAuth tokens, tax ID, telehealth connections, stored identifiers | AES-256-GCM under `GOOGLE_CALENDAR_ENCRYPTION_KEY`, with a derived subkey per purpose | `backend/app/services/token_encryption.py` |

There is no field-level encryption of clinical text. Access control and the
audit log are the controls on it.

**The reverse proxy.** Caddy is the only way in, and `TRUSTED_PROXY_IPS`
names it. The backend believes `X-Forwarded-Proto` and `X-Forwarded-For`
only from a listed peer, and counts sign-in, passkey, portal-code and
booking limits per client address taken from that header. If you put a
different proxy in front of the backend, list its address instead; an
unlisted proxy makes every client share the proxy's one rate-limit budget,
and with an empty list every request is refused as plain HTTP. Details in
[Settings and keys](self-hosting/settings-and-keys.md).

## Audit logging

Every route that touches a client's record writes a row to that practice's
`audit_logs` table: who, what, which record, when, from which address.
`docs/HIPAA_AUDIT_LOGS.md` has the shape and the covered actions.

- **Append-only.** Triggers block `UPDATE`, `DELETE` and `TRUNCATE` on the
  table, even for the table's owner. This stops the app and any ordinary
  mistake. It is defence in depth, not an absolute wall against someone
  with the database password; the sink below is the independent copy.
- **Sign-ins.** The backend records a `signed_in` row on the first request
  of each sign-in (`backend/app/auth/sign_in_audit.py`). Failed password
  attempts are not in `audit_logs`; Identity Platform handles lockout.
- **The mirror.** Each audit row is also written to Cloud Logging as
  `pablo.audit_events`, and the sink copies it to the retention bucket.
  Write failures there are logged and do not fail the request; the
  database row is the record. (`backend/app/services/audit_cloud_logging.py`)
- **Retention.** Rows are kept 2555 days (7 years), then deleted by
  `audit_retention_cron`, daily at 02:30 UTC. The bucket keeps its copy
  2255 days (about 6 years and 2 months); the installer locks that policy,
  so it cannot be shortened.
- **Your own view.** Clinicians can read their own audit log in the app,
  and see who acted on a client's record.
- **Postgres-level logging.** `pgaudit` is loaded and records role and DDL
  changes made by other database users; it is switched off for the app's
  own role. Its output stays in the VM's Postgres log. It is not a second
  record of PHI access. (`deploy/vm/bootstrap.sh`)

The daily and monthly reviews of this log, and what they produce, are in
[Operations](self-hosting/operations.md#scheduled-jobs).

## Data retention and deletion

What the code does today, not what is planned.

- **Deleting a client** sets `deleted_at`. The record disappears from
  lists and can be restored for 30 days. Before deleting, the clinician
  confirms they have met their own record-retention obligations.
  (`backend/app/routes/patients.py`)
- **After 30 days the client is permanently removed**: the record, notes,
  sessions and their audio, appointments, chat history and uploaded
  documents. `hard_purge_cron` does this daily at 02:00 UTC on the VM, and
  nightly before the backup on a local install.
- **What remains** is one audit entry saying the client was purged, with
  the internal ID and the time. No name or date of birth is kept.
- **Session audio** can also be deleted sooner: when a note is signed, or
  after a number of days you choose, from the app's settings.
- **Backups** hold whatever the database held when they were taken, so a
  purged client stays in them until they age out: about 4 to 5 weeks on the
  VM (four weekly full backups and their differentials; disk snapshots are
  kept 14 days), 30 days on a local install.
- **Audit rows** are kept 2555 days (7 years), then deleted (above).

## A client's right of access

`GET /api/patients/{id}/export?format=zip` produces the chart as a PDF, the
same data as JSON with its schema, uploaded documents, intake forms and
billing statements. The export is recorded in the audit log, including
whether transcripts or psychotherapy notes were included (both off by
default). Format and contents: [docs/reference/export-format.md](reference/export-format.md).

## Business Associate Agreements

| Vendor | Carries PHI when | Agreement | How Pablo helps |
|---|---|---|---|
| Google Cloud | Always: the VM, disks, buckets, Secret Manager, Identity Platform, Cloud Logging, Cloud Tasks | Google Cloud BAA, accepted once in the console | The installer refuses to proceed until you say it is accepted |
| Google Vertex AI (Gemini) | Every note draft | Under the Google Cloud BAA, per Google's covered-services list | `GOOGLE_GENAI_USE_VERTEXAI=true` keeps calls inside your project; the public Gemini API is not used |
| AssemblyAI | Session audio, if transcription is on | Direct BAA with AssemblyAI | The installer asks; saying yes is your statement that it is signed |
| Your email provider | Portal invitations, booking confirmations, if you configure SMTP | Your BAA with that provider | Off until you set `SMTP_*`; an invitation names the client and the practice, which is PHI |
| Pablo Health | Never | None needed | — |

**Two things to confirm with Google before real sessions go through
Vertex.** The installer sets `VERTEX_REGION=global`, the global endpoint,
and a preview model. This repository cannot confirm that the global
endpoint and preview models are inside the BAA's covered-services list, or
what Google's current data-use terms for them say. Ask Google, or set
`VERTEX_REGION` to a specific US region in `deploy/vm/lib/env.sh` if your
answer needs that, and keep the answer with your BAA records.

`docs/compliance/baa-manifest.yaml` lists every hostname the backend can
reach and which agreement covers it.

## Running on a workstation instead

`./install.sh --target local` runs the same containers on a Mac or PC behind
Tailscale Funnel. **Evaluation only.** Funnel relays the TLS connection
through Tailscale's servers, where the certificate lives, so the encrypted
session terminates at a third party before it reaches your machine. The
machine also holds the records, so its disk, screen lock and uptime become
your controls. For a practice's records use the VM.
(`deploy/vm/lib/local.sh`, `deploy/workstation/README.md`)

## Keeping it current

Security fixes reach you only when you apply them.

```bash
gcloud compute ssh pablo --zone=<zone> --tunnel-through-iap
sudo pablo-update vX.Y.Z
```

An update takes about a minute of downtime. Watch
[pablo-health/pablo releases](https://github.com/pablo-health/pablo/releases)
and read the notes before applying. Security advisories are published through
GitHub; `SECURITY.md` says how to report one.

## Compliance documents in this repository

- `docs/compliance/hipaa-security-rule.md`: the Security Rule control by
  control. Written for Pablo Health's hosted service; the rows marked
  operator-responsibility are yours when you self-host.
- `docs/HIPAA_AUDIT_LOGS.md`: the audit log in detail.
- `docs/HIPAA_SECURITY.md`: TLS on the VM.
- `docs/compliance/baa-manifest.yaml`: outbound destinations and their
  agreements.
