# Technical Architecture

**Project:** Pablo
**Last Updated:** 2026-07-02
**Status:** Active Development

## Table of Contents

1. [System Overview](#system-overview)
2. [Technology Stack](#technology-stack)
3. [Backend Architecture](#backend-architecture)
4. [Data Layer & Tenant Isolation](#data-layer--tenant-isolation)
5. [Authentication](#authentication)
6. [Frontend Architecture](#frontend-architecture)
7. [Application Features](#application-features)
8. [Feature configuration](#feature-configuration)
9. [Security & HIPAA Compliance](#security--hipaa-compliance)
10. [Deployment](#deployment)
11. [Testing](#testing)
12. [Contributing](#contributing)

---

## System Overview

Pablo is a HIPAA-compliant web application that helps therapists produce
clinical documentation from session transcripts. SOAP notes (Subjective,
Objective, Assessment, Plan) are generated with an AI pipeline and verified
with a dual-method approach (LLM plus classical NLP), then reviewed and
finalized by the clinician.

### Core Functionality

- **Patient management** - clinicians manage patient records
- **Session management** - upload transcripts (VTT, JSON, or TXT) and track status
- **SOAP generation** - AI drafts SOAP notes for review
- **Review & finalize** - clinicians edit and sign off on notes
- **Patient-context chat** - grounded, patient-scoped chat with source
  citations and audit logging
- **Compliance reminders** - a catalog of trackable compliance items
  (license renewal, CAQH attestation, and similar) with reminders
- **Scheduling and related clinical workflows** - appointments, outcome
  measures, medications, and documents

Some features are gated per deployment (see [Feature configuration](#feature-configuration)).

### Architecture Style

- **Frontend:** Next.js App Router single-page application
- **Backend:** RESTful API built with FastAPI
- **Data:** PostgreSQL with SQLAlchemy, schema-per-practice multi-tenant
  isolation enforced by Postgres row-level security (RLS)
- **Auth:** Firebase Authentication / Google Identity Platform
- **Hosting:** Google Cloud Run

---

## Technology Stack

### Frontend

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Framework | Next.js 16 (App Router) | React framework |
| UI | React 19 | Component-based UI |
| Language | TypeScript | Type-safe JavaScript |
| Styling | Tailwind CSS + shadcn/ui | Utility-first CSS and components |
| Auth | Firebase Auth + `next-firebase-auth-edge` | Sign-in and edge token verification |
| Data fetching | TanStack Query | Server state management |
| PDF export | jsPDF | SOAP note export |
| Testing | Vitest, Playwright | Unit/component and E2E testing |
| Runtime | Node.js 24 | Build and dev server |

Typography uses **DM Sans** for body text and **Fraunces** for headings.

### Backend

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Framework | FastAPI | Async Python web framework |
| Language | Python 3.13+ | Type-annotated Python |
| ORM | SQLAlchemy 2 | Data access |
| Migrations | Alembic | Schema versioning |
| Database | PostgreSQL 16 | Relational store (Cloud SQL in production) |
| Auth | firebase-admin | Firebase token verification |
| Validation | Pydantic 2 | Request/response validation |
| Tooling | Ruff, mypy, pytest | Lint, type-check, test |

### Infrastructure

| Component | Technology | Purpose |
|-----------|-----------|---------|
| Hosting | Google Cloud Run | Serverless container hosting |
| Database | Cloud SQL (PostgreSQL 16) | Production database |
| Authentication | Firebase / Identity Platform | User management |
| Secrets | Secret Manager | API keys and credentials |
| Logging | Cloud Logging | Structured logs and audit sink |
| CI/CD | GitHub Actions | Automated testing and deployment |
| Local dev | Docker Compose | Local environment |

---

## Backend Architecture

The backend follows a layered design:

- **Routes** (`backend/app/routes/`) - request validation, response shaping,
  HTTP status codes, and audit logging for PHI-touching endpoints.
- **Services** (`backend/app/services/`) - business logic, including
  SOAP generation and `AuditService`.
- **Repositories** (`backend/app/repositories/`) - all data access. Concrete
  Postgres implementations live in `backend/app/repositories/postgres/`; the
  package root defines the interfaces used by the rest of the app. Route
  handlers never issue raw SQL directly — they go through repositories so
  tenant scoping and error shapes stay in one place.
- **Database layer** (`backend/app/db/`) - SQLAlchemy models, Alembic
  migrations, tenant provisioning, and RLS setup.

Every SQLAlchemy model change ships with a matching Alembic migration in the
same commit, and any migration that touches tenant DDL regenerates the tenant
schema template (`backend/app/db/tenant_template.sql`), which is the canonical
schema applied to every freshly provisioned practice.

---

## Data Layer & Tenant Isolation

Pablo is multi-tenant with **one Postgres schema per practice**. When a
practice is provisioned, `create_practice_schema` builds its schema from the
tenant template and `enable_rls_on_schema` turns on **row-level security** for
every per-tenant table that carries a `user_id`, `patient_id`, or `id` column.

Isolation is enforced at two levels:

1. **Schema separation** - each practice's data lives in its own schema.
2. **Row-level security** - within a schema, RLS policies scope rows to the
   requesting clinician so one clinician cannot read or write another's
   patient rows, even if a query is misconstructed.

New per-tenant tables must be classified (given an RLS policy or explicitly
registered as not row-scoped); an unclassified table ships as deny-all by
design. This contract is checked by unit and integration tests that seed two
clinicians and assert cross-tenant reads and writes are rejected.

---

## Authentication

Authentication uses **Firebase Authentication (Google Identity Platform)**.

- The frontend Firebase auth package lives under
  `frontend/src/lib/auth/` (client SDK, login and MFA screens, and server
  helpers in `frontend/src/lib/auth/firebase/`). Edge middleware verifies
  session cookies with `next-firebase-auth-edge`.
- The backend verifies Firebase ID tokens with `firebase-admin` in
  `backend/app/auth/`, extracts the user identity, and provides it to routes
  via FastAPI dependency injection.
- Access to PHI requires multi-factor authentication. Both TOTP multi-factor
  enrollment and phishing-resistant **passkey (WebAuthn)** sign-in are
  supported — the passkey ceremony (register/authenticate, backup codes) lives
  in `backend/app/routes/passkey.py` and `backend/app/services/passkey_*`, with
  the enrollment UI at `frontend/src/components/settings/PasskeySettings.tsx`.

Requests carry a Firebase ID token (`Authorization: Bearer <token>`); the
backend rejects missing or invalid tokens.

---

## Frontend Architecture

The frontend is a Next.js App Router application. Route groups under `app/`
provide a shared authenticated dashboard shell; feature UI and shared
components live under `src/components/`, and cross-cutting utilities
(API client, auth, config) under `src/lib/`. Data fetching uses TanStack
Query, and styling uses Tailwind CSS with shadcn/ui on the Pablo brand
palette. See `frontend/README.md` for setup and `docs/design-system/` for
design tokens.

---

## Application Features

Beyond the core document workflow, two features are worth calling out because
they cut across the stack.

### Patient-context chat

A grounded, patient-scoped chat surface. The backend route
(`backend/app/routes/chat.py`) and turn service
(`backend/app/services/chat_turn_service.py`) assemble context strictly from
the selected patient's records, run the model turn, and return answers with
source citations back to the underlying records; every turn is audit-logged
like any other PHI access. The route is registered conditionally in
`backend/app/main.py` behind the `enable_patient_chat` setting. The frontend
lives in `frontend/src/components/chat/` (`ChatPanel`, `Composer`,
`ChatHistorySidebar`, `BriefingCard`, `ScopeFooter`, and source-citation
chips). The underlying primitive is documented in
[`docs/architecture/patient-context-chat-oss.md`](./architecture/patient-context-chat-oss.md).

### Compliance reminders

A catalog of trackable compliance items for a clinician — professional license
renewal, CAQH attestation, HIPAA training, malpractice insurance, and similar.
The template catalog lives in `backend/app/compliance/templates.py`, and
user-entered items are stored in a per-tenant `compliance_items` table
(`backend/app/db/models.py`). Items carry a cadence (recurring) or a fixed
expiration date, so reminders fire before a lapse. This feature is
edition-gated.

---

## Feature configuration

Feature availability is configured per deployment via the non-secret
`pablo_edition` setting in `backend/app/settings.py`, one of `core`, `solo`,
or `practice`. `core` is the default and the self-hosted open-source
configuration.

Some features (for example, the additional compliance-reminder templates) and
behaviors are gated on this setting and on individual per-feature flags — for
instance, `practice` enables per-practice multi-tenancy, routing requests to
per-practice PostgreSQL schemas. This is the only mechanism for feature
gating; there is no hidden configuration beyond these settings.

---

## Security & HIPAA Compliance

### Encryption

- **In transit:** HTTPS enforced, TLS 1.2+.
- **At rest:** Cloud SQL storage encryption; secrets in Secret Manager.

### Access control and audit (shipped)

- **Firebase authentication** on every request, with MFA and passkey support.
- **Automatic idle logoff** - a backend-enforced idle session timeout
  (`backend/app/auth/idle_session.py`) rejects requests whose session has been
  idle past the configured window, so a restored tab or replayed token cannot
  silently keep touching PHI. HIPAA §164.312(a)(2)(iii).
- **Application-level audit logging** - `AuditService`
  (`backend/app/services/audit_service.py`) records every PHI-touching request:
  who, what, when, source IP/user agent, and the change diff for mutations.
  New PHI routes are required to log through it (enforced in CI).
  HIPAA §164.312(b).
- **6-year audit retention** - audit records are retained for at least six
  years per §164.316(b)(2)(i), with append-only/anti-truncate protections on
  the audit trail. See `docs/HIPAA_AUDIT_LOGS.md`.
- **No PHI in logs** - patient data never enters stdout; intentional
  PHI-adjacent records go through `AuditService`.

### Tenant isolation

Schema-per-practice plus Postgres RLS (see
[Data Layer & Tenant Isolation](#data-layer--tenant-isolation)).

---

## Deployment

Pablo runs on Google Cloud Run (frontend and backend containers) with Cloud
SQL for PostgreSQL, Firebase for authentication, Secret Manager for
credentials, and Cloud Logging for logs and the audit sink.

For step-by-step deployment and compliance configuration, see:

- **[docs/GCP_DEPLOYMENT.md](./GCP_DEPLOYMENT.md)** - deploying to Google Cloud.
- **[docs/SELF_HOSTING_HIPAA_GUIDE.md](./SELF_HOSTING_HIPAA_GUIDE.md)** -
  self-hosting with the HIPAA controls in place.

---

## Testing

- **Backend:** pytest for unit and integration tests, including cross-tenant
  isolation tests that seed two clinicians and assert they cannot access each
  other's rows. Lint and types via Ruff and mypy.
- **Frontend:** Vitest with React Testing Library for unit/component tests and
  Playwright for end-to-end flows.

Run the full local gate before opening a PR:

```bash
make check   # lint (ruff + mypy, eslint) and tests (pytest, vitest)
```

---

## Contributing

Open a pull request against `main`. Ensure `make check` passes locally
(linting, type-checking, and tests) before requesting review.

---

### Glossary

- **BAA** - Business Associate Agreement (HIPAA)
- **HIPAA** - Health Insurance Portability and Accountability Act
- **PHI** - Protected Health Information
- **RLS** - Row-Level Security (PostgreSQL)
- **SOAP** - Subjective, Objective, Assessment, Plan (clinical note format)

### Related Documentation

- [HIPAA Audit Logs](./HIPAA_AUDIT_LOGS.md)
- [GCP Deployment](./GCP_DEPLOYMENT.md)
- [Self-Hosting HIPAA Guide](./SELF_HOSTING_HIPAA_GUIDE.md)
