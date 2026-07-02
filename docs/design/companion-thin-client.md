# Companion as a thin handoff target

**Status:** Proposed
**Authors:** Kurt Niemi
**Last updated:** 2026-05-16

## Summary

The Pablo Companion desktop app currently tries to be a full clinical UI
on every install — its own dashboard, day view, session history,
patient list, settings. That duplicates surface area the web app already
owns (and owns better, since the web app is where therapists actually
spend their day).

This doc proposes shrinking the companion to a **thin handoff target**:
the web app is the dashboard, the companion runs in the background to
do the things only a desktop process can do (audio capture, system
audio loopback, encrypted-at-rest storage), and the in-browser "Start
Session" button hands off to the companion via a domain-verified deep
link rather than a custom URL scheme.

Existing native dashboard / day-view / session-history code is **kept in
the repo behind a feature flag** so we can re-enable a full-fat desktop
experience later if the product calls for it. Nothing is deleted.

## Goals

1. The web dashboard is the single place a therapist sees their day.
2. The companion has a **minimal main window**: connection status,
   account, "Open Web Dashboard" button, version info. Nothing else.
3. After first-time OAuth completes, the browser tab the user
   authorized in lands on the web dashboard — not on a "you can close
   this tab" page.
4. The "Start Session" button on the web dashboard hands off to the
   companion via a **domain-verified deep link** (Universal Link on
   macOS, App URI Handler on Windows MSIX) — not the current
   `pablohealth://session/start` custom scheme.
5. The companion shows an **affirmative confirmation** ("Start session
   with [Patient Name]?") before arming the microphone, even when
   handed off from the web.

## Non-goals

- Removing or rewriting native dashboard code. It stays behind a flag.
- Replacing the existing OAuth flow. PKCE + loopback (Mac) / PKCE +
  `pablohealth://callback` (Windows MSIX) stay as the enrollment
  primitive. See [Enrollment](#enrollment) below for what gets layered
  on top.
- Shipping DPoP / hardware-bound tokens in the first cut. Tracked as
  follow-on hardening work, not a launch blocker for the thin-client
  shape.
- Touching the existing `pablohealth://` scheme registrations — they
  remain for OAuth callback and as a fallback for browsers that don't
  honor Universal Links / App URI Handlers (Firefox on both platforms).

## Why this matters

Three independent pressures point the same direction:

1. **The "wow page" landing flow.** The new marketing/landing page
   is the first thing a prospective therapist
   sees. The conversion moment is "click here to start a session" —
   that click should land on the web dashboard, not on a desktop UI
   the user has never seen before. The desktop UI is a second mental
   model to learn, and it duplicates information that's already on
   the page they just landed on.
2. **Security posture for handoff.** Today the web app emits
   `pablohealth://session/start?appointment=<id>` (see
   `docs/url-scheme.md`). Custom URL schemes are not domain-verified
   — any installed app can claim the scheme. For a PHI-adjacent
   handoff (the URL points at an appointment ID) we want
   domain-verified routing (Universal Links / App URI Handlers) so
   the OS can guarantee the deep link reaches *our* companion, not
   an impostor.
3. **Maintenance cost of two dashboards.** Day-view, session list,
   patient list, and settings exist in three places today: web
   (Next.js), mac (SwiftUI), Windows (WinUI 3). Each new feature
   pays a 3× tax. Concentrating the day-to-day UX on the web cuts
   that to 1× and lets the native apps focus on what they're
   uniquely good at (audio + system integration).

## Proposed product shape

```
┌──────────────────────────────┐         ┌──────────────────────────────┐
│   Browser (web dashboard)    │         │      Pablo Companion         │
│   pablo.health/dashboard     │         │  (minimal main window)       │
│                              │         │                              │
│   • Day view                 │         │   ● Connected to pablo.health│
│   • Session history          │  ─────► │   ● Mic ready                │
│   • Patient list             │  deep   │   ● Version 1.4.2            │
│   • [Start Session] button   │  link   │                              │
│                              │         │   [ Open Web Dashboard  ]    │
└──────────────────────────────┘         └──────────────────────────────┘
              ▲                                       │
              │  post-OAuth                           │  during session:
              │  redirect                             │  ephemeral session
              │                                       │  window (existing
              │                                       │  RecordingControlsView /
              │                                       │  PracticeSessionView)
              │                                       │
         OAuth flow ──────────────────────────────────┘
```

## Companion UI changes

### New main window — minimal

After authentication, `ContentView.swift` (Mac) and the equivalent
WinUI root view show a single small window containing:

- **Status block.** "Connected to pablo.health as <email>" + green/red
  dot. Mic-ready indicator. Update-available indicator if applicable.
- **Primary CTA.** `Open Web Dashboard` button — calls
  `NSWorkspace.shared.open(URL(string: "https://app.pablo.health/dashboard"))`
  on Mac and `Launcher.LaunchUriAsync` on Windows.
- **Footer.** Account / sign-out / preferences / version. No tabs, no
  session lists, no patient lists.

Window size target: ~480×360 pt. Designed to be glanced at, not lived
in.

### What gets feature-flagged

A new compile-time / runtime flag (working name
`ENABLE_NATIVE_DASHBOARD`, default `false`) gates the existing tab-bar
shell. When `true`, the companion behaves as it does today: four-tab
nav (today, sessions, patients, settings). When `false`, only the
minimal window is shown. **No existing view files are deleted.**
`DayView.swift`, `SessionHistoryView.swift`, `PatientListView.swift`,
their viewmodels, their tests — all stay.

Reasoning: the native dashboard is real work that may be the right
answer in the future (offline-first, faster than the web, a richer
desktop experience for power users). Cheaper to keep it dormant than
to rewrite it.

### What stays live in the companion

- OAuth flow + token storage (`LoginView`, `KeychainManager`,
  `TokenRefresher`).
- Recording surface — when a session is in progress, the existing
  `RecordingControlsView` / `PracticeSessionView` opens as an ephemeral
  window. Closed when the session ends.
- Background work — token refresh, idle-logout timer, pending-upload
  retry, recording watchdog. All unchanged.
- Settings / preferences — accessible from the minimal window's
  footer.

## Enrollment — what "secure auto-login" actually means

The companion's first-launch OAuth IS the device enrollment. We don't
need a separate certificate ceremony. We do need to make the OAuth
callback do two extra things:

1. **Register a device identity with the backend** at code exchange
   time. The companion generates an `install_id` (random UUID, stored
   in Keychain / Credential Manager) and POSTs
   `{install_id, platform, os_version, hostname_hash}` alongside the
   authorization code. The backend stores
   `(user_id, install_id, enrolled_at, last_seen)` and binds the
   issued refresh token to that `install_id`.
2. **Redirect the browser to the web dashboard.** After the loopback
   server (Mac) or `pablohealth://callback` handler (Windows) finishes
   the code exchange, the OAuth completion page in the browser
   navigates to `https://app.pablo.health/dashboard?from=companion`.
   This is what makes "after OAuth, you're on the dashboard" land —
   it's a server-side redirect in the OAuth completion handler, not
   the companion driving the browser.

### Hardware-bound device key (in v1)

> **Update (post-design):** the original draft of this doc deferred
> hardware-bound DPoP to a follow-on phase. After threat-model
> review, this was promoted into v1 so we don't ship throwaway
> install_id-as-bearer auth. The full security architecture —
> including our deviation from RFC 9449 (binding to `install_id`
> instead of access-token `cnf.jkt`) and the per-scenario threat
> table — is in
> [`companion-dpop-binding.md`](./companion-dpop-binding.md).

The companion generates a non-extractable P-256 keypair at enrollment:

- Mac: `SecKeyCreateRandomKey` with `kSecAttrTokenIDSecureEnclave`.
  Coverage: 100% of Macs from 2018+ have Secure Enclave.
- Windows: `CngKey.Create` with `Microsoft Platform Crypto Provider`
  (TPM 2.0). Graceful fallback to
  `Microsoft Software Key Storage Provider` (DPAPI-bound user-profile
  key) on TPM-less machines, with `key_storage='software'` recorded
  in the device row. Coverage: ~95% hardware-bound, 100% with the
  software fallback.

The public key is registered with the backend at the same time as
`install_id`. Every API call thereafter carries a `DPoP` header
(RFC 9449-style) signed by the private key, plus an `X-Install-ID`
header identifying which enrolled device is calling. A stolen
Firebase id_token without the corresponding hardware key becomes
useless.

**Staged rollout (so we don't ship the middleware before companions
support signing):**

1. **Stage 1** (backend): `companion_devices` table,
   enrollment payload accepted at `/api/auth/native/exchange`,
   stored but not enforced.
2. **Stage 2** (backend): `DPoP` middleware enforces
   per-request proofs. Behind `ENABLE_DPOP_VALIDATION` until
   companions ship.
3. **Stage 3** (companion repo): Secure Enclave / TPM
   key generation, JWK serialization, per-request proof signing on
   Mac and Windows.
4. **Stage 4 (optional)**: App Attest / TPM AIK enrollment-time
   attestation. Closes the "scripted fake companion enrolls itself"
   gap; lower priority.

## Deep-link handoff — the secure wow-button flow

### Today's flow (custom scheme, spoofable)

1. Web dashboard renders `<a href="pablohealth://session/start?appointment=123">`.
2. Click → browser shows "Open Pablo Companion?" consent prompt.
3. Companion launches, dispatches the URI, fetches appointment 123 via
   authenticated API, opens session.

Problems: any installed app can claim `pablohealth://` and intercept
the appointment ID. The browser prompt is the only safety net.

### Proposed flow (domain-verified + launch intent)

0. **Smart detection on render.** When the dashboard page mounts, it
   calls `GET /me/devices` (new endpoint, returns the list of
   enrolled companion installs for the current user). If the user
   has at least one enrolled install, the wow-page button renders as
   `Start Session`. If not, it renders as `Download Pablo Companion`
   pointing at the download page. No failed launches, no "click and
   nothing happens" UX.
1. User clicks `Start Session` on web dashboard.
2. Web → backend: `POST /launch/intent {appointment_id}` →
   backend returns opaque `intent_id` (128-bit random, **180s TTL**,
   single-use, bound to `user_id`).
3. Browser navigates to
   `https://app.pablo.health/launch/<intent_id>` —
   the OS routes this to the companion via Universal Link (Mac) /
   App URI Handler (Windows MSIX), with the existing
   `pablohealth://` scheme as fallback for unsupported browsers.
4. Companion receives the URL, calls
   `POST /launch/redeem {intent_id}` with its existing access token.
5. Backend verifies: token belongs to same user the intent was issued
   to, intent unused, not expired. Marks intent consumed. Returns
   `{appointment_id, patient_name, video_url, session_id}`.
6. Companion opens an ephemeral session window with
   **"Start session with [Patient Name]?"** + a single `Start
   Recording` button. Mic does not arm until the therapist taps it.

### Why the launch-intent indirection

The web could embed `appointment_id` directly in the URL and skip the
intent step. We don't, for three reasons:

1. The URL is visible to the OS, browser history, screen recorders,
   any installed app that successfully claims the scheme as a
   fallback. An opaque `intent_id` is single-use and worthless to an
   attacker; an appointment ID is a stable pointer.
2. The redemption step is where we enforce that the device receiving
   the intent is the device authenticated as the user who issued it.
   Without redemption, there is no server-side checkpoint on the
   handoff.
3. It gives us a single audit-log entry per handoff
   (`audit_service.log_event("launch_intent_redeemed", ...)`) that
   ties the web click to the companion-side session start.

### Platform requirements

| Surface | Requirement | Lives in |
|---|---|---|
| Mac AASA file | Served at `https://<host>/.well-known/apple-app-site-association` listing `<TeamID>.health.pablo.companion` with `applinks` paths `/launch/*` | Next.js Route Handler in OSS frontend (env-configured) |
| Mac entitlement | `com.apple.developer.associated-domains = ["applinks:app.pablo.health", "applinks:dev.pablo.health"]` (both envs in one build) | `mac/PabloCompanion/PabloCompanion.entitlements` |
| Windows manifest | `<uap3:AppUriHandler>` with `<uap3:Host Name="app.pablo.health"/>` and `<uap3:Host Name="dev.pablo.health"/>` | `windows/PabloCompanion/Package.appxmanifest` |
| Windows JSON | Served at `https://<host>/.well-known/web-credentials` listing the package family name | Next.js Route Handler in OSS frontend (env-configured) |
| Backend | `POST /launch/intent`, `POST /launch/redeem`, `GET /me/devices` | `backend/app/routes/launch.py`, `backend/app/routes/me.py` (new / extend) |
| Frontend | `GET /me/devices` smart-detection; emit `https://app.pablo.health/launch/<id>` for enrolled users; route handler files served from OSS frontend | `frontend/src/components/dashboard/TodayPanel.tsx`, `frontend/app/.well-known/apple-app-site-association/route.ts`, `frontend/app/.well-known/web-credentials/route.ts` |

### Hosting: Next.js Route Handlers, env-configured

The two well-known files live in the OSS frontend repo (`pablo-health/pablo`)
as Next.js Route Handlers, not the FastAPI backend, not a CDN. Reasoning:

- The web domain belongs to the frontend (`app.pablo.health` is the
  Next.js app, not the backend API). Files describing what handles
  that domain belong there.
- Same code, two deploys: `dev.pablo.health` (dev env) and
  `app.pablo.health` (prod env). The route handler reads
  `AASA_TEAM_ID`, `AASA_BUNDLE_ID`, `WEB_CREDENTIALS_PFN` from env,
  so the values track the deploy.
- Self-hosters get the template "for free" — the route handler is
  generic; they just set their own env vars to their TeamID / PFN /
  bundle ID and serve their own AASA from their own domain.
- Mac entitlement lists both hosts in the same signed build
  (`applinks:app.pablo.health` AND `applinks:dev.pablo.health`), so
  one binary serves both environments. Same for the Windows
  AppUriHandler manifest — both hosts listed.

### Firefox / unsupported browser fallback

Universal Links and App URI Handlers are not honored by Firefox on
either platform. The frontend feature-detects (or falls back
unconditionally if the redirect doesn't fire within ~1s) to the
existing `pablohealth://session/start?appointment=<id>` scheme. The
custom scheme stays registered for exactly this fallback — and for
OAuth callback on Windows MSIX, which already uses it.

## Migration / rollout

1. **Backend launch-intent endpoint + `/me/devices` + frontend
   route handlers for AASA / web-credentials.** Land in OSS pablo.
   Backend behind a feature flag (`ENABLE_LAUNCH_INTENT`, default
   `false`) until clients are ready. Route handlers ship with env
   vars unset on first deploy (returns 404 until configured), then
   env vars are set per environment. Self-hosters get the same
   primitives.
2. **Companion: Mac Associated Domains entitlement + Windows
   AppUriHandler manifest change.** Ship in a normal companion release.
   No user-visible behavior change until the backend flag flips.
3. **Companion: launch-intent redemption + "Start session with X?"
   confirmation UI.** Wired into existing `DeepLinkRouter`. Falls
   through to existing `session/start` handler when the URL is the
   legacy scheme.
4. **Companion: minimal main window behind
   `ENABLE_NATIVE_DASHBOARD=false`.** Ship as a normal release. The
   old shell is one flag flip away.
5. **Frontend: emit `https://app.pablo.health/launch/<id>` from the
   wow button.** Land after backend `ENABLE_LAUNCH_INTENT` flips. Keep
   the `pablohealth://` emitter as Firefox fallback.
6. **Backend: post-OAuth completion page redirects to
   `/dashboard?from=companion`.** Small change in
   `backend/app/routes/auth.py`'s native-callback handler.

Order matters: backend before clients before frontend. Each step is
independently shippable.

## Resolved decisions

- **AASA / web-credentials hosting:** Next.js Route Handlers in the
  OSS frontend repo, env-configured for dev and prod hosts. See
  §"Hosting" above.
- **Launch-intent TTL:** 180 seconds (3 minutes). Tight enough that
  intercepted tokens have a narrow replay window; forgiving enough
  for OS consent prompts.
- **First-time user UX on the wow button:** smart detection via
  `GET /me/devices`. Renders as `Start Session` when an install is
  enrolled, `Download Pablo Companion` otherwise. No failed launches.
- **Self-hosters and Universal Links:** OSS the template (the Next.js
  route handler IS the template, generic + env-configured).
  Self-hosters set their own `AASA_TEAM_ID` / `AASA_BUNDLE_ID` /
  `WEB_CREDENTIALS_PFN` env vars, ship their own signed companion
  build pointing at their domain. We don't multi-tenant the AASA file
  itself — each self-hosted deploy is single-tenant.

## Still open / deferred

- **iOS / mobile companion.** Not in scope today. If/when there's an
  iOS companion, the same Universal Link primitive applies — that's
  partly why we're picking this design.

## Out of scope (deliberately)

- Replacing OAuth + PKCE with anything else (DPoP layers on top, not
  replaces).
- Deleting any native dashboard code.
- Mobile / iOS companion app.
- Self-hosted OSS users getting a deployment-branded Universal Link
  experience.
- Cross-device session handoff (start on laptop, finish on iPad). Not
  asked for, not designed for.

## Related

- [`companion-dpop-binding.md`](./companion-dpop-binding.md) — full
  security architecture for the hardware-bound device key, including
  our deviation from RFC 9449 and the per-scenario threat table.
- `docs/url-scheme.md` — the existing `pablohealth://` URI grammar
  (still applies; this doc augments it with the verified-link path).
- Marketing/landing page (the "wow page" this flow plugs into).
- Frontend idle-logout for HIPAA (the auto-record
  concern in §"Affirmative confirmation" is directly related; both
  exist for the same compliance posture).
