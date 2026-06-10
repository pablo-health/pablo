# `pablohealth://` URL scheme

The Pablo desktop companion app registers `pablohealth://` as its
custom URL scheme. The web dashboard uses these links to hand off
recording-side actions to the companion (the web app has no in-browser
recording capability — capture is desktop-only).

## Grammar

```
pablohealth://<resource>/<action>?<params>
```

- `<resource>` mirrors the backend route prefix (`session`, `patient`,
  `auth`, …).
- `<action>` is the verb (`start`, `open`, `callback`, …).
- `<params>` carry **opaque IDs only**. No PHI in the URL — names,
  emails, dates of birth, transcript text never appear.

The companion always re-fetches the underlying record from the
backend after activation, so URLs are intentionally minimal — they
say "what to do" and "to which row," not "with what content."

`session/start` carries a single-use **launch intent** id, not a raw
appointment id. The web issues the intent server-side, scoped to the
signed-in user, and the companion redeems it once over the
authenticated API to learn which appointment to record. A leaked or
guessed link is therefore inert — it isn't a standing pointer at a
patient's appointment.

## Registered URIs

| URI | Purpose | Status |
|---|---|---|
| `pablohealth://callback?code=...&state=...` | OAuth code-exchange callback (RFC 8252 §7.1). | **Live** — web → companion auth, Google Calendar OAuth. |
| `pablohealth://session/start?intent=<id>` | Start recording for the given appointment, identified by a single-use launch intent the companion redeems server-side (180s TTL). Companion launches if not running. | **Live (web)** — emitted by `frontend/src/components/dashboard/StartSessionButton.tsx` and the `/launch/<id>` fallback page, as the fallback to the domain-verified `https://<host>/launch/<id>` link. Companion handler TBD. |
| `pablohealth://session/start?patient=<id>` | Quick-start: open the companion's "Start session for X" sheet. | **Reserved** — for future "+ Quick Start" buttons in patient pages. |
| `pablohealth://session/open?session=<id>` | Open an existing session record in the companion. | **Reserved**. |
| `pablohealth://patient/<id>` | Open a patient view in the companion. | **Reserved**. |

## Compatibility rules

1. **Never break a shipped URI.** A web build emitted in 2026 will
   still be in browser caches in 2027; the companion must keep handling
   the URIs we've shipped. To deprecate, add a new resource/action
   alongside the old one and wait at least one major release before
   removing the handler.
2. **No PHI, ever.** If a future feature looks like it needs to pass
   a name or transcript through the URL, redesign — pass an ID and
   let the companion fetch it over the authenticated API.
3. **Resource names match backend prefixes.** Adding a new route under
   `backend/app/routes/foo.py` and a new `pablohealth://foo/...` URI
   should feel like one decision, not two.
4. **Single-purpose URIs.** Don't overload one URI with multiple
   query-string toggles — make a new action verb instead. Cheaper for
   the companion to dispatch on, easier to log in audit trails.
5. **Allowlist server-side where the URI flows through us.** Today
   the auth flow validates redirect URIs against
   `ALLOWED_NATIVE_SCHEMES` in `backend/app/routes/auth.py`. The
   dashboard-emitted URIs (`session/start`, etc.) never traverse the
   backend — the browser opens them directly — so no backend allowlist
   is needed. If a future flow adds a server-mediated redirect, add it
   to the allowlist there.

## Where to look

| Surface | File |
|---|---|
| Web emitter | `frontend/src/components/dashboard/StartSessionButton.tsx`, `frontend/app/launch/[intentId]/page.tsx` |
| Backend allowlist (OAuth only) | `backend/app/routes/auth.py`, `backend/app/routes/scheduling.py` |
| Windows protocol registration | `pablo-companion/windows/PabloCompanion/Package.appxmanifest` |
| Windows activation handler | `pablo-companion/windows/PabloCompanion/Services/ProtocolActivationListener.cs` |
| macOS protocol registration | `pablo-companion/mac/.../Info.plist` (`CFBundleURLTypes`) |
| macOS activation handler | `PabloCompanionApp.swift` `onOpenURL` |

## Adding a new URI

1. Edit this file first — pick a resource and action that match the
   pattern.
2. Implement the web emitter (typically a button in the dashboard or
   a patient/session page).
3. File a companion-side ticket to add the dispatch case in both
   Windows and macOS handlers.
4. Don't ship the web emitter ahead of companion support: a click on
   an unhandled scheme is silent on most OSes and feels broken.
