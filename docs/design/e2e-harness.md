# End-to-end test harness

**Status:** Design, 2026-09-06.
**Scope:** a Playwright harness that drives the whole product on one
machine — browser, API, database, and every outbound integration the
product makes — with no cloud project and no vendor credentials.

## Problem

`frontend/e2e/patients.spec.ts` is a single scaffold with no
authentication and no way to run in CI. Unit and route tests prove each
layer alone; the bugs that reach users live between layers: a form that
posts a shape the API rejects, a webhook the worker never consumes, a
signed-URL flow that works in tests and fails in a browser. Two such bugs
were found this week in the card-payment path only after a browser drove
the deployed app. Every new surface (coverage, claims, booking) should be
provable the same way before it ships, on a laptop, in this repository.

## Solution

### Stack under test: `docker-compose.e2e.yml`

Extends the existing `docker-compose.yml` (`backend`, `postgres`) with:

| Service | Image / source | Why |
|---|---|---|
| `frontend` | `frontend/` built in production mode | the real bundle, not `next dev` |
| `firebase-auth` | `ghcr.io/…/firebase-tools` emulator, `auth` only | the backend already honours `FIREBASE_AUTH_EMULATOR_HOST` (`backend/app/auth/firebase_init.py`); the frontend gains `connectAuthEmulator` behind `NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_HOST` |
| `fake-clearinghouse` | `scripts/fake_clearinghouse.py` (FastAPI) | serves the recorded responses in `backend/tests/fixtures/clearinghouse/` for payer search, eligibility, claim submission, enrollment, polling and reports, and posts `transaction processed` webhooks back to the backend on a scripted delay |

The backend's clearinghouse base URL and webhook secret are plain
settings, so pointing them at the fake is configuration, not code. The
fake is deterministic: a claim whose control number starts `REJ` gets the
recorded edit rejection; anything else gets the recorded success, a 277CA
after 2 s, and an 835 after 5 s. Nothing here talks to the internet.

`make e2e-up` brings the stack up and migrates; `make e2e` runs the suite;
`make e2e-down` tears it down. Playwright's `webServer` block waits on the
frontend's health route.

### Harness layout

```
frontend/e2e/
  playwright.config.ts        baseURL, webServer, one chromium project
  fixtures/
    auth.ts                   create an emulator user via the emulator REST
                              API, sign in through the real login page once,
                              save storageState; `signedInPage` fixture
    api.ts                    bearer-token client for "given X" setup calls
    scenarios.ts              givePatient, giveCoverage, giveSessionWithCodes,
                              giveBookingLink — API-level state, never UI
    clearinghouse.ts          drive the fake: trigger the 277CA / 835 for a
                              control number, list what it received
  specs/
    patients.spec.ts          the existing spec, rewritten onto the fixtures
    claims.spec.ts            coverage → file claim → tracker submitted →
                              277CA → payer_accepted → 835 → paid
    public-booking.spec.ts    the anonymous booking path end to end
```

Fixture idioms are deliberately conventional so a spec written here reads
the same against any deployed environment: tiered fixtures
(`onboardedUser` → `signedInPage`), API-driven "given" helpers, assertions
on what the user sees plus one out-of-band check where the interesting
state lives (the claim row's state, the fake clearinghouse's received
submissions).

### Authentication

The emulator issues real-shaped ID tokens the backend verifies with no
credentials. `fixtures/auth.ts` creates the user through the emulator's
REST endpoint, then signs in through the product's own login page so the
login flow itself is under test once per run. MFA is not enrolled for the
e2e user; the flag that requires it is off in the e2e compose profile.

### CI

One `e2e` job in `ci.yml`: compose up, `npx playwright test`, upload the
HTML report and traces on failure, compose down. It runs nightly on
`main`, on demand, and on pull requests that change the frontend, the
backend routes, the compose files, the fake clearinghouse, or
`frontend/e2e/`. Not on every push: a full-stack run costs minutes and
the nightly catches drift. The job is a required check once it has run
green for a week.

The live vendor lane (below) is its own workflow: weekly, on demand, and
on pull requests that touch the clearinghouse adapter, the live suite or
the recorded fixtures, with the test key held as a repository secret that
fork pull requests never receive.

## The live vendor lane

The fake clearinghouse proves this code; it cannot prove the vendor still
behaves as the recordings say. A second, opt-in suite under
`backend/tests_integration/clearinghouse_live/` runs the same operations
against the clearinghouse's test mode with a deployment's own test key:
payer search, mock eligibility (active, inactive, error), the submission
edits that reject on purpose (each asserting our scrub would have caught
it first), one successful test claim with an idempotency key replayed and
then changed, and the poll until the test payer's acknowledgment and
remittance arrive. Every test diffs the live response's shape against the
recorded fixture so a vendor change fails loudly. It skips without a key
and never reaches a real payer.

## What this is not

- Not a replacement for the deployed-environment suite that proves cloud
  wiring (signed URLs, real auth, real vendors). That suite stays where the
  deployment lives.
- Not visual regression.
- Not a load test; one worker, serial specs, deterministic fakes.

## Rollout

1. Harness + emulator + fake clearinghouse + rewritten `patients.spec.ts`
   + the CI job definition, in one change.
2. `claims.spec.ts` when the claims Billing surface lands.
3. `public-booking.spec.ts` next; then every new user-facing surface ships
   with its spec here as part of done.
