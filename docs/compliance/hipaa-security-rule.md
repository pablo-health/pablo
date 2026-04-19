# Pablo — HIPAA Security Rule Control Narrative

**Scope:** this document maps each 45 CFR §164 Security Rule control to how
Pablo (the deployed Cloud Run + Firebase + Cloud SQL application) meets it,
with pointers to the code, workflow, or runtime configuration that provides
the evidence. It complements — but does not replace — the weekly pentest
report produced by `/pentest`; the pentest covers what is *technically
observable at runtime*, this document covers the paper and process controls
plus the CI/CD safeguards that run before any code reaches production.

**Audience:** HHS OCR auditors, Covered Entity compliance teams evaluating
Pablo as a Business Associate, and Pablo engineers confirming a change did
not weaken a stated control.

**Update cadence:** reviewed every time a control row moves — code change,
policy change, or NPRM clarification. Annual re-attestation per §164.308(a)(8).

**Status legend:**
- **Met** — the control is fully implemented and continuously verified.
- **Met (CI/CD-enforced)** — enforced by an automated gate on every change;
  regression is not possible without bypassing a workflow.
- **Partial** — implemented technically; paper artifacts (policy, signed
  attestation, training record) are tracked outside this repository.
- **Operator-responsibility** — self-hosters must implement in their own
  environment. Pablo provides tooling/config; the Covered Entity executes.

**2024 NPRM note:** HHS's 2024 Notice of Proposed Rulemaking is expected
to finalize in 2026. Rows flagged **(NPRM)** anticipate requirements that
are currently "addressable" becoming "required" — Pablo treats them as
required today.

---

## 1. CI/CD as a compliance control surface

Pablo's delivery pipeline is itself a HIPAA technical safeguard. Every
change to PHI-touching code passes through these gates before it can be
deployed — regression on a safeguard is caught pre-merge, not post-
incident.

| Gate | File | What it enforces | §164 control supported |
|---|---|---|---|
| Version consistency | `.github/workflows/ci.yml` → `version-check` | Backend/frontend/min-client versions move together | §164.308(a)(5)(ii)(B) protection against malicious software (rollback safety) |
| Lint (ruff + mypy) | `.github/workflows/ci.yml` → `backend` | Type safety + forbidden patterns (raw SQL in routes, PHI in logs) | §164.312(c)(1) integrity |
| Unit + integration tests (pytest) | `.github/workflows/ci.yml` → `backend` | Tenant-scoping, audit logging, auth guards | §164.312(a)(1), §164.312(b) |
| pip-audit (Python deps) | `.github/workflows/ci.yml` → `backend` | No known-vulnerable Python package may merge | §164.308(a)(5)(ii)(B), §164.308(a)(8) |
| npm audit (frontend) | `.github/workflows/ci.yml` → `frontend` | `--audit-level=high` gate on frontend deps | §164.308(a)(5)(ii)(B) |
| Frontend type-check + lint + vitest | `.github/workflows/ci.yml` → `frontend` | No type errors, no lint violations, test pass | §164.312(c)(1) |
| CodeQL | `.github/workflows/codeql.yml` | SAST for injection/auth bugs | §164.308(a)(8), §164.312(b) |
| Trivy — dependency lockfiles | `.github/workflows/security.yml` → `trivy-deps` | CRITICAL/HIGH CVE block at PR + weekly | §164.308(a)(5)(ii)(B) |
| Trivy — base images | `.github/workflows/security.yml` → `trivy-base-images` | Chainguard Python + Node bases scanned weekly | §164.308(a)(5)(ii)(B) |
| Trivy — Dockerfile config | `.github/workflows/security.yml` → `trivy-config` | DS-0001/0002/0026 misconfiguration gating | §164.312(a)(1), §164.312(e)(1) |
| Release build | `.github/workflows/release.yml` | SHA-tagged images published only from `main` or `v*` tags | §164.312(c)(1), §164.312(c)(2) |
| Branch protection (GitHub) | Repo settings | No direct push to `main`; PR + reviews required | §164.308(a)(3)(i), §164.308(a)(4)(ii)(C) |
| Weekly pentest | `.claude/skills/pentest/SKILL.md`, `backend/app/jobs/pentest_runner.py` | Full HIPAA-grade pentest on deployed Cloud Run + Cloud SQL | §164.308(a)(8) (NPRM annual pentest + biannual vuln scan) |

Every row above has a machine-readable run log (GitHub Actions) that an
auditor can inspect directly; no human attestation is needed to verify
the gate ran.

---

## 2. Administrative safeguards (§164.308)

| Control | Requirement | Status | Evidence |
|---|---|---|---|
| §164.308(a)(1)(ii)(A) | Risk analysis — accurate, thorough, documented; reviewed ≥12mo | Met | Weekly pentest report in `gs://<COMPLIANCE_REPORT_BUCKET>/pentest/` + annual third-party pentest (operator). `docs/HIPAA_SECURITY.md` records the current risk register. |
| §164.308(a)(1)(ii)(B) | Risk management — reduce risks to reasonable level | Met | Findings from §6 of each pentest report are tracked with severity-tiered target-resolution windows (CRIT ≤7d, HIGH ≤30d, MED ≤90d, LOW ≤180d). |
| §164.308(a)(1)(ii)(C) | Sanction policy | Partial / Operator-responsibility | Workforce sanction policy is a paper control — Covered Entity owns. Pablo provides evidence of *detection* (audit logs); sanction procedures live in the operator's HR policy. |
| §164.308(a)(1)(ii)(D) | Information system activity review — logs, access reports, incident reports | **Met (CI/CD-enforced)** | `backend/app/services/audit_service.py` + `backend/app/jobs/hipaa_log_review.py` (scheduled compliance job). `audit_logs` schema is PHI-free by design — enforced by `backend/tests/` and by pentest §13 each week. |
| §164.308(a)(2) | Assigned security responsibility | Partial / Operator-responsibility | Pablo Health LLC owns the hosted product; self-hosting operators designate their own security official. |
| §164.308(a)(3)(i) | Workforce authorization / supervision | Met | Firebase Auth + Google Workspace RBAC; admin routes gated by `/api/admin/*` → `ADMIN_REQUIRED` (`backend/app/routes/admin.py`). |
| §164.308(a)(3)(ii)(A) | Workforce clearance | Partial / Operator-responsibility | Covered Entity-owned paper control. |
| §164.308(a)(3)(ii)(B) | Workforce authorization | Met | Role enforcement in `backend/app/routes/admin.py` + tenant scoping in repositories. |
| §164.308(a)(3)(ii)(C) | Termination procedures — revoke access on departure | Met | Firebase Auth account disable + `UPDATE users SET is_active = false` via admin route. Pentest §11 verifies the admin endpoints exist and require MFA. |
| §164.308(a)(4)(ii)(A) | Isolating healthcare clearinghouse functions | N/A | Pablo is not a clearinghouse. |
| §164.308(a)(4)(ii)(B) | Access authorization — granted per role | Met | `clinician`, `admin`, `owner` roles enforced in route handlers; tenant isolation enforced in repositories (see CLAUDE.md guardrail #2 — "no raw SQL in route handlers"). |
| §164.308(a)(4)(ii)(C) | Access establishment & modification | Met | Admin routes create/modify users; every change is audit-logged. |
| §164.308(a)(5)(i) | Security awareness & training | Partial / Operator-responsibility | Covered Entity owns workforce training. Pablo publishes `docs/SELF_HOSTING_HIPAA_GUIDE.md` for operator-level awareness. |
| §164.308(a)(5)(ii)(A) | Security reminders | Operator-responsibility | — |
| §164.308(a)(5)(ii)(B) | Protection from malicious software | **Met (CI/CD-enforced)** | Trivy CRITICAL/HIGH gate (`.github/workflows/security.yml`), pip-audit + npm audit on every PR, CodeQL SAST, weekly re-scan of deployed base images. Chainguard zero-CVE bases (`backend/Dockerfile.production`). |
| §164.308(a)(5)(ii)(C) | Log-in monitoring — detect anomalies | Met | Firebase Auth anti-abuse (strict bad-password lockout); `audit_logs` records every authentication event. Pentest §9 probes with ≤10 bad passwords per rule #1. |
| §164.308(a)(5)(ii)(D) | Password management (**NPRM**: MFA required) | Met | Firebase Auth TOTP MFA is required for all clinician accounts; JWT carries `firebase.sign_in_second_factor=totp`. Pentest §4 verifies this claim is present on issued tokens. |
| §164.308(a)(6)(i)-(ii) | Security incident response & reporting | Partial / Operator-responsibility | `docs/HIPAA_AUDIT_LOGS.md` documents the audit trail; incident response runbook is operator-owned. Pablo's audit logs provide the forensic record. |
| §164.308(a)(7)(i) | Contingency plan | Partial / Operator-responsibility | Cloud SQL automated backups + point-in-time recovery (operator-configured). |
| §164.308(a)(7)(ii)(A) | Data backup plan — tested | Met | Cloud SQL daily automated backups with 7-day retention by default; PITR enabled. `docs/GCP_DEPLOYMENT.md` documents the operator-tunable retention. |
| §164.308(a)(7)(ii)(B) | Disaster recovery plan — restore ≤72h (**NPRM**) | Met | Cloud SQL PITR + container image retention in GHCR (see `release.yml` — images SHA-tagged for rollback). Cloud Run rollback is sub-5min. |
| §164.308(a)(7)(ii)(C) | Emergency mode operation | Partial / Operator-responsibility | — |
| §164.308(a)(7)(ii)(D) | Contingency plan testing ≥12mo | Operator-responsibility | Covered Entity owns disaster recovery tests. Pablo's rollback is exercised implicitly by every release. |
| §164.308(a)(7)(ii)(E) | Applications & data criticality analysis | Partial / Operator-responsibility | `docs/TECHNICAL_ARCHITECTURE.md` documents the data tiers. |
| §164.308(a)(8) | Technical evaluation — annual pentest + biannual vuln scan (**NPRM**) | **Met (CI/CD-enforced)** | Weekly automated pentest (`/pentest`) + weekly Trivy scan (`security.yml` cron) + CodeQL on every PR. Annual third-party pentest is operator-responsibility. |

---

## 3. Physical safeguards (§164.310)

Pablo runs on Google Cloud Platform (Cloud Run, Cloud SQL, GCS, Secret
Manager, Firebase). Physical safeguards for the data-center layer are
inherited from GCP's BAA. All rows below are therefore **Met
(inherited from GCP BAA)** for the hosted product; self-hosting operators
inherit from their chosen cloud provider's BAA.

| Control | Requirement | Status | Evidence |
|---|---|---|---|
| §164.310(a)(1) | Facility access controls | Met (inherited) | GCP BAA + SOC 2 Type II. |
| §164.310(a)(2)(i)-(iv) | Contingency ops / facility security plan / access control & validation / maintenance records | Met (inherited) | GCP data-center certifications. |
| §164.310(b) | Workstation use | Partial / Operator-responsibility | Covered Entity workstation policy. |
| §164.310(c) | Workstation security | Partial / Operator-responsibility | Covered Entity workstation policy. |
| §164.310(d)(1) | Device & media controls | Met (inherited) | GCP persistent-disk handling; Pablo does not process media on workforce devices. |
| §164.310(d)(2)(i) | Disposal | Met (inherited) | GCP media sanitization. |
| §164.310(d)(2)(ii) | Media re-use | Met (inherited) | GCP. |
| §164.310(d)(2)(iii) | Accountability | Met | GCS object retention + Cloud SQL backup retention tracked per-resource. |
| §164.310(d)(2)(iv) | Data backup & storage | Met | Cloud SQL automated backups + GCS versioning on the compliance bucket. |

---

## 4. Technical safeguards (§164.312) — the core of Pablo's HIPAA posture

This is the section the weekly pentest re-verifies from the outside each
run. Every row has an observable runtime artifact.

| Control | Requirement | Status | Evidence |
|---|---|---|---|
| §164.312(a)(1) | Unique user identification | Met | Firebase UID per user; `firebase_uid` column on `users` table. JWT carries the UID; it is stamped into every `audit_logs` row. Tenant isolation enforced in `backend/app/repositories/` (CLAUDE.md guardrail #2). |
| §164.312(a)(2)(i) | Unique user identification (req) | Met | Same as above. |
| §164.312(a)(2)(ii) | Emergency access procedure | Met | Admin role + owner role can impersonate/reset; every impersonation is audit-logged. |
| §164.312(a)(2)(iii) | Automatic logoff / session timeout | Met | Firebase ID tokens expire after 1h; refresh-token revocation on logout. Frontend forces re-auth on expiry. |
| §164.312(a)(2)(iv) | Encryption / decryption of ePHI at rest (**NPRM: required**) | Met | Cloud SQL AES-256 at rest (GCP-managed keys by default; CMEK operator-configurable). GCS default encryption. Secret Manager encryption at rest. |
| §164.312(b) | Audit controls — record & examine activity | **Met (CI/CD-enforced)** | `backend/app/services/audit_service.py` + `backend/app/repositories/audit.py`. Scheduled review in `backend/app/jobs/hipaa_log_review.py`. CLAUDE.md guardrail #1 enforces that every PHI route must take an `AuditService` dependency — pentest §13 and code review verify. Schema is PHI-free by design (CLAUDE.md guardrail #4). |
| §164.312(c)(1) | Integrity — protect ePHI from improper alteration/destruction | Met | Tenant-scoped repositories; admin-only deletion; row versioning on clinical models; audit log captures every mutation. |
| §164.312(c)(2) | Mechanism to authenticate ePHI (**NPRM**) | Met | Cloud SQL + application-layer tenant checks ensure rows can only be read/written by authorized users; audit log provides chain of custody. |
| §164.312(d) | Person or entity authentication (**NPRM: MFA required**) | **Met (CI/CD-enforced)** | Firebase TOTP MFA required; JWT `firebase.sign_in_second_factor=totp` verified server-side. Pentest §4 probes this on every run. Frontend enforces MFA enrollment. |
| §164.312(e)(1) | Transmission security | Met | HTTPS-only on Cloud Run (auto-managed TLS). HSTS header set; `testssl.sh` in pentest §1 verifies TLS 1.2+ only, no weak suites. |
| §164.312(e)(2)(i) | Integrity controls in transit | Met | TLS integrity; Cloud SQL uses TLS to the backend; `cloud-sql-proxy` encrypted channel. |
| §164.312(e)(2)(ii) | Encryption in transit (**NPRM: required**) | Met | Same as §164.312(e)(1); no plaintext HTTP. |

---

## 5. Organizational requirements (§164.314)

| Control | Requirement | Status | Evidence |
|---|---|---|---|
| §164.314(a)(1) | Business Associate contracts & other arrangements | Partial / Operator-responsibility | Pablo Health LLC offers a BAA to Covered Entity customers; self-hosters execute their own BAAs with subprocessors. |
| §164.314(a)(2)(i)-(iii) | BAA written contract; reasonable assurance; reporting | Partial / Operator-responsibility | BAA template + subprocessor list maintained outside this repo. |
| §164.314(b)(1)-(2) | Group health plan requirements | N/A | Pablo is not a group health plan. |

**Subprocessor observability:** pentest §3 (Asset inventory & data flow)
enumerates every external hostname the backend can reach (via
`grep -rEoh "https?://[a-zA-Z0-9.-]+" /app/backend`) and flags any
non-Vertex inference endpoints (`api.anthropic.com`, `api.openai.com`,
public Gemini) as a MEDIUM finding so the operator can reconcile against
the BAA-covered subprocessor list.

---

## 6. Policies & procedures + documentation (§164.316)

| Control | Requirement | Status | Evidence |
|---|---|---|---|
| §164.316(a) | Policies & procedures | Partial | `docs/HIPAA_SECURITY.md`, `docs/HIPAA_AUDIT_LOGS.md`, `docs/SELF_HOSTING_HIPAA_GUIDE.md`, this document. |
| §164.316(b)(1) | Documentation | Met | Source-controlled (`docs/`), Git history is the change log. |
| §164.316(b)(2)(i) | Time limit — retain ≥6 years | Met | Git history preserves all policy docs indefinitely. Pentest reports retained in `gs://<COMPLIANCE_REPORT_BUCKET>/pentest/` with retention lock. |
| §164.316(b)(2)(ii) | Availability | Met | Public repo + published docs. |
| §164.316(b)(2)(iii) | Updates | Met | This document is re-reviewed whenever a referenced workflow, file, or control changes; Git commit on the change constitutes the update record. |

---

## 7. Breach Notification Rule (§164.400–414) — interaction points

Pablo is the BA, not the Covered Entity. The CE notifies affected
individuals and HHS; Pablo notifies the CE. Operational support from
Pablo for a breach investigation:

- **Audit log export** — `backend/app/jobs/hipaa_log_review.py` can
  produce a PHI-free access report for any date range, redacted by
  design.
- **Incident reconstruction** — SHA-tagged container images in GHCR
  (`release.yml`) allow exact reconstruction of the code running at
  breach time.
- **Pentest trail** — the weekly pentest history in
  `gs://<COMPLIANCE_REPORT_BUCKET>/pentest/` establishes that the
  control environment was actively monitored.

---

## 8. Annual written verification (§164.314(a) — 2024 NPRM)

The 2024 NPRM, anticipated to finalize in 2026, requires a Business
Associate to provide its Covered Entity customers with **annual written
verification** that the BA's Security Rule safeguards are in place.
Pablo's annual verification is assembled from:

1. This document (control narrative, updated within 12 months).
2. The most recent weekly pentest report from
   `gs://<COMPLIANCE_REPORT_BUCKET>/pentest/` (Appendix E attestation
   block, counter-signed by the Security Official).
3. The most recent independent third-party pentest report (operator
   provides).
4. The current Trivy / CodeQL / pip-audit dashboard state (GitHub
   Security tab — auditor-inspectable).

The counter-signed weekly pentest attestation is the machine-verifiable
anchor; this document is the human-readable narrative; the third-party
pentest is the independent check. Together they constitute §164.308(a)(8)
evidence.

---

## 9. Change log

- **2026-04-18** — Initial version. Written concurrent with the
  `pentest-hipaa-refactor` branch, which split the weekly pentest
  pipeline into deterministic Python collectors + LLM narrative +
  deterministic assembly so that the weekly control verification no
  longer depends on LLM prompt discipline for report completeness.
