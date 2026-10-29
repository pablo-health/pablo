# Settings and keys

Every security-relevant setting the VM install uses, what it does, and the
value `deploy/vm/lib/env.sh` writes. Companion to the
[Self-hosting HIPAA guide](../SELF_HOSTING_HIPAA_GUIDE.md).

The backend reads its settings from `/etc/pablo/backend.env` (root, mode
0600), rendered by `pablo-update` from `/etc/pablo/config.env` and Secret
Manager. Edit `deploy/vm/lib/env.sh` in a fork, or the rendered file for a
one-off test, and run `sudo pablo-update` to apply. Definitions:
`backend/app/settings.py`.

## Backend settings

| Variable | VM value | Effect | If wrong |
|---|---|---|---|
| `ENVIRONMENT` | `production` | Drives the security posture. Outside `development`: plain HTTP is refused, HSTS is sent, `DEBUG` and `REQUIRE_MFA=false` stop the app from starting, and the test-only base-URL overrides are refused | `development` on a reachable server turns all of that off. Never set it there |
| `REQUIRE_MFA` | unset (default `true`) | Every token must carry a second factor | `false` is refused outside `development`; the app will not boot |
| `IDLE_TIMEOUT_SECONDS` | unset (default `900`) | A session with no request for this long is ended by the backend. Needs Redis | Raising it weakens automatic logoff; the browser's warning (`frontend/src/components/IdleTimeout.tsx`) assumes 15 minutes |
| `IDLE_SESSION_FAIL_OPEN` | unset (default `false`) | When the Redis check errors, requests are refused with 503 | `true` keeps the site up during a Redis outage at the cost of logoff enforcement |
| `USE_REDIS` | `true` | Shared state: the idle clock, rate limits, passkey challenges, calendar-connect handshakes | `false` makes the idle timeout a no-op and the rate limiter per-process in memory; the app logs a warning at boot outside development. Connecting a calendar needs it |
| `TRUSTED_PROXY_IPS` | `172.30.0.10` (Caddy's fixed address) | Only a listed peer may set `X-Forwarded-Proto` and `X-Forwarded-For`. HTTPS detection and the rate limiter both use it (`backend/app/trusted_proxy.py`); limits for sign-in, passkeys, portal codes and public booking are counted per client address | Empty: every request through Caddy looks like plain HTTP and is refused. `*`: trust any peer, only safe where nothing but the platform can reach the port. A different proxy left unlisted makes every client share one rate-limit budget |
| `TRUSTED_PROXY_HOPS` | unset (default `1`) | How many trusted proxies sit in front; the client address is read that many entries from the right of `X-Forwarded-For` | Add 1 per extra proxy you put in front of Caddy |
| `FRONTEND_FORWARD_SECRET` | from Secret Manager | Lets the web app's server pass the real user's address and agent to the backend for audit rows, under a shared secret. The backend accepts a comma-separated list during rotation | Empty: server-rendered requests are audited with the frontend's address instead of the user's |
| `RESTRICT_SIGNUPS` | `true` | Only allowlisted emails may sign in | `false` lets anyone with the address create an account |
| `AUDIT_DUAL_WRITE_ENABLED` | unset (default `true`) | Mirrors every audit row to Cloud Logging `pablo.audit_events`, which the sink keeps six years | `false` leaves the database as the only copy |
| `AUDIT_READ_COALESCE_SECONDS` | unset (default `0`) | Collapses repeated views of the same record by the same user within this window into one row. Needs Redis | Default keeps one row per read |
| `GOOGLE_CALENDAR_ENCRYPTION_KEY` | from Secret Manager (32 random bytes, base64) | AES-256-GCM key for data at rest that is encrypted in the application: calendar OAuth tokens, telehealth connections, the practice tax ID, stored identifiers and more, each under a subkey derived per purpose (`backend/app/services/token_encryption.py`) | Lost or changed: everything encrypted under it is unreadable. See [rotation](#rotating-keys) |
| `PORTAL_TOKEN_SIGNING_KEY` | unset | Signs patient portal invite and session tokens and peppers the one-time code | Empty fails closed: the portal service refuses to construct (`backend/app/portal/service.py`). Set it only when you turn the portal on |
| `PORTAL_INVITE_DELIVERY`, `PORTAL_SMS_GATEWAY` | unset (`none`) | How invitations and codes reach clients | `none` answers 503 rather than pretend. `console` and `capture` gateways are refused outside development |
| `EMAIL_BACKEND`, `SMTP_*` | unset (`none`) | Outbound email with STARTTLS | Anything that needs email stays off. Your provider needs a BAA |
| `GOOGLE_GENAI_USE_VERTEXAI`, `VERTEX_REGION` | `true`, `global` | Notes go to Gemini through Vertex AI in your project | Confirm global-endpoint coverage with Google, or set a US region |
| `TRANSCRIPTION_ENABLED`, `ASSEMBLYAI_API_KEY` | from the installer's answer | Session audio to AssemblyAI | Needs your BAA with AssemblyAI |
| `DEBUG` | unset (`false`) | Interactive tracebacks | Refused outside `development` |

## Frontend settings

| Variable | VM value | Effect |
|---|---|---|
| `AUTH_SECRET`, `AUTH_COOKIE_SIGNATURE_KEY` | from Secret Manager | Sign the web app's own session cookies. Changing them signs everyone out |
| `FRONTEND_FORWARD_SECRET` | same value as the backend | See above |
| `PASSKEYS_ENABLED` | `true` | Passkeys as a sign-in method, scoped to `PABLO_HOST` |
| `ENABLE_LOCAL_AUTH`, `DEV_MODE` | `false` | Development-only sign-in paths, off |
| `GOOGLE_CALENDAR_ENABLED` | `true` if you set up the OAuth client | Calendar sync |

## Where the secrets live

`deploy/vm/lib/secrets.sh` creates each of these once, in Secret Manager,
and a later run of `install.sh` never regenerates them. The VM's service
account can read them; nothing else needs to.

| Secret | Used by |
|---|---|
| `pablo-db-password` | Postgres role `pablo`; the backend's `DATABASE_URL` |
| `GOOGLE_CALENDAR_ENCRYPTION_KEY` | Backend, application-level encryption |
| `AUTH_SECRET`, `AUTH_COOKIE_SIGNATURE_KEY` | Frontend sessions |
| `FRONTEND_FORWARD_SECRET` | Both |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google sign-in and calendar, if chosen |
| `pablo-assemblyai-api-key` | Transcription, if chosen |

Access to each secret is in Google's admin activity log, which the audit
sink keeps.

## Rotating keys

`pablo-update` renders the env files from the latest version of each secret
and restarts the stack, so the general shape is: add a version, run
`pablo-update`.

**Database password.** On the VM:

```bash
new=$(openssl rand -hex 32)
sudo -u postgres psql -c "ALTER ROLE pablo PASSWORD '$new'"
printf '%s' "$new" | gcloud secrets versions add pablo-db-password --data-file=-
sudo pablo-update
```

The app is unable to connect between the `ALTER ROLE` and the restart;
keep the two close together.

**`FRONTEND_FORWARD_SECRET`.** Both services read the same secret and
restart together, so one new version then `pablo-update` is enough. The
backend's comma-separated form exists for deployments that restart the two
separately.

**`AUTH_SECRET`, `AUTH_COOKIE_SIGNATURE_KEY`.** New version, `pablo-update`.
Every clinician signs in again.

**`GOOGLE_CALENDAR_ENCRYPTION_KEY`: there is no rotation path.** The code
holds one key and no previous one (`token_encryption.py`). A new key makes
every value encrypted under the old one unreadable: calendar connections,
telehealth connections, the practice tax ID, stored identifiers. Treat the
key as permanent, keep it in Secret Manager only, and if it is ever
exposed, plan to re-enter those values after changing it. Generate one with
`openssl rand -base64 32`.

**Google OAuth client secret.** Rotate in the Google console, add a version
of `GOOGLE_CLIENT_SECRET`, `pablo-update`. Clinicians reconnect their
calendars if Google invalidates existing grants.

**AssemblyAI key.** New key from AssemblyAI, add a version of
`pablo-assemblyai-api-key`, `pablo-update`.

**SSH and project access** are IAM, not secrets: remove the person's role on
the project and their OS Login access ends with it.
