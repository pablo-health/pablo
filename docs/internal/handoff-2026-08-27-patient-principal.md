# Handoff: the patient principal (pablo#772) — 2026-08-27

Written for a session starting cold. Picks up from
`pablo-saas/docs/internal/handoff-2026-08-26-consent-and-fleet.md`, which is
still accurate about the consent design, the fleet and the traps. This covers
only the patient-principal work.

## Read this first: what is and is not verified

**`pablo#772` is OPEN, green on CI at `8664db9`, and Kurt has said he is fine
with it going in.** Do not treat that as permission to push more.

There is **one local commit past what CI has seen**:

| Commit | State |
|---|---|
| `8664db9` | pushed, CI green, all checks pass |
| `b57e156` | **local only, NOT pushed, NOT verified** |

`b57e156` carries the second security review's fixes and two new test files.
Its unit tests pass (2634) and each new integration file passes **on its own**.
What I could **not** do is run the full `tests_integration/` suite to
completion — see "The unresolved thing" below. **Do not push `b57e156` until
that suite runs green**, because two of its changes touch `arm_current_user_id`,
which is on many clinician paths.

## What landed, and why

`pablo#771` (`audit_logs.actor_type`) merged as `84f9fb0` — that was item 1 of
the previous handoff.

`pablo#772` is `u37i.1` (the patient principal) **plus `u37i.3`** (patient-scoped
RLS policies). Kurt asked for `.3` in the same PR after asking "I feel actually
testing that this works on our first table is important" — which was the right
call, and `.3` registers exactly one core table so it fits.

Design decisions worth not relitigating:

- **Two GUCs, not one.** `app.current_patient_id` is separate from
  `app.current_user_id`. A shared "current principal" GUC would let
  `USING (patient_id = current_setting(...))` accept a clinician whose user id
  happened to equal a patient id, and would leave every existing clinician
  policy satisfiable by a patient. There is a test for exactly that collision.
- **`PatientContext` is not a `TenantContext` subclass.** A common base class is
  how a patient principal eventually satisfies a dependency that meant to ask
  for a clinician.
- **Patient-readability is an explicit registry** (`PATIENT_READABLE_TABLES`),
  not column inference. Plenty of tables carry a `patient_id` without the
  patient being entitled to read them — `notes` is the clinician's record
  *about* a patient, not *for* them. Read and write are separate registries.
- **`get_patient_context` must stay `async`.** This is load-bearing, not style.
  See the next section.

## The two HIGH bugs, because the class will recur

Both were found by review, both were silent in the **default** configuration,
and both defeated a property the code's own docstrings claimed.

**1. The clinician guard was dead code.** `get_patient_context` refuses a
credential the middleware already verified as a clinician's. But the
verify-and-stash step sat inside `if settings.multi_tenancy_enabled:`, which
defaults to `False`. On a single-tenant install — the default, and what a
self-hosted companion runs — nothing ever set the value and the guard never
fired. Fixed in `8664db9`.

Then Codex found the *second* way in: the stash matched `"Bearer "`
case-sensitively while FastAPI's `HTTPBearer` matches `scheme.lower()`. So
`Authorization: bearer <clinician-token>` authenticates fine on clinician routes
but skipped the stash — one lowercase letter and a clinician's token reached
every patient resolver. Fixed in `b57e156`.

**2. The patient's `search_path` did not survive a mid-request commit.**
FastAPI runs a *sync* dependency in a throwaway threadpool worker whose context
copy is discarded on return. `set_tenant_schema` writes a ContextVar; the GUC
survived that hop because it also rides `Session.info`, and the schema had no
such carrier. After the first mid-request commit released the connection, the
next checkout re-stamped `search_path` from whatever the *middleware* left —
`DEFAULT_PRACTICE_SCHEMA`, the shared template — while the patient GUC stayed
correctly armed. The rest of the request would read and write the template
schema under a live patient identity.

Fixed by making the dependency `async`, verified empirically both directions
(a sync dep's ContextVar set is lost; an async dep's survives). **A regression
test fails if anyone reverts it to `def`.**

## The unresolved thing — start here

**The full `tests_integration/` suite does not complete on this machine.** It
hangs on `tests_integration/database/test_audit_writes.py::TestLogPatientAction::
test_writes_row_for_each_patient_action[patient_created]` — an **existing** test
that runs before any of the new files.

Symptom: elapsed time climbs for tens of minutes while CPU stays at a few
seconds. In Postgres, one connection sits `idle in transaction` on an
`INSERT INTO audit_logs`, and `pg_blocking_pids` reports nothing blocked. So it
is waiting in Python, not on a database lock.

**What I ruled out, so you don't repeat it:**

- Not an interaction with the new test files — `test_audit_writes.py` hangs when
  run entirely on its own.
- Not `_disarm_other_principal`. I disabled it outright with an A/B edit and the
  hang persisted. (That marker is reverted; `grep -n "AB-TEST\|MUTATION-TEST"`
  over `backend/app/` returns nothing.)
- Not obviously resource exhaustion, though it contributed: my killed runs left
  **six orphaned testcontainers** because this conftest sets
  `TESTCONTAINERS_RYUK_DISABLED=true`, so nothing reaps them. I removed those
  six; the hang still reproduced afterwards.

**What I did not get to check, in the order I would try it:**

1. Whether this hangs on `origin/main` too. That is the single most valuable
   next data point and I should have done it first — it decides whether this is
   ours at all. Use a separate worktree at `84f9fb0`; do **not** `git stash`
   (shared stack, per the repo's CLAUDE.md).
2. Whether it hangs in CI. CI was green on `8664db9`, including
   `Backend integration (Postgres)`, which is evidence it may be local-only —
   possibly Docker Desktop resource pressure on this Mac.
3. `pytest --timeout` (pytest-timeout may need installing) plus `faulthandler`
   to get a stack dump of where Python is parked.

**Every time you kill a run, `docker ps` and remove the orphaned
`postgres:16-alpine` container.** They accumulate silently and degrade
everything afterwards. Leave `pablo-saas-*`, `pablo-postgres-1`,
`dramellea-*`, `buildx_*` alone — those are Kurt's, not test containers.

## Testing, and what each layer is actually for

Four suites, deliberately not redundant:

- `backend/tests/test_patient_context.py` — units. **Its dependency tests
  monkeypatch the DB-arming path**, which is precisely why they could not catch
  bug 2. Do not add security coverage here and think you are done.
- `tests_integration/database/test_patient_guc_integration.py` — GUC mechanics
  against real Postgres.
- `tests_integration/database/test_patient_principal_rls.py` — two-patient
  isolation via direct SQL on a **real provisioned schema** with the real
  shipped policies.
- `tests_integration/database/test_patient_idor_http.py` (new, unpushed) — the
  same question through **real HTTP with nothing mocked**.

**The IDOR file's design, since it looks wrong at a glance.** It has two route
shapes. `/patient/record/{patient_id}` deliberately has *no ownership check*, so
RLS is the only control under test — a route that also checked would pass even
if every policy were broken. `/patient/me/record` is the shape real routes
should copy: it takes no id from the client at all and scopes by
`context.patient_id`, so there is no ownership question to get wrong. **Do not
copy the `{patient_id}` routes into production.**

**Mutation-test anything you add here.** Everything above was mutation-tested
and I would not trust it otherwise:

| Mutation | Result |
|---|---|
| Real predicate → `USING (true)` | 10 fail |
| `patients` unregistered | 5 fail |
| Canary policy → `USING (true)` | 8 fail |
| `get_patient_context` → `def` | 4 fail |

## Traps this session hit

- **Running `tests/` and `tests_integration/` in one pytest invocation silently
  disables the integration suite.** `tests/conftest.py:28` plants a dummy
  `DATABASE_URL` at import time; `tests_integration/conftest.py`'s
  `pytest_configure` sees it, assumes a real database, returns early and never
  sets `DATABASE_BACKEND=postgres` — so every integration module skips. Exit 0,
  looks like a pass. Argument order does not help. **Run them separately.**
  Worth fixing in the conftest; I left it alone as out of scope.
- **`poetry run` in an OSS worktree makes an empty in-project `.venv`** and
  subprocess-based tests (`alembic upgrade head`) then resolve to system Python
  3.10 and fail with `'type' object is not subscriptable`. Either
  `poetry install` in the worktree or use the main venv directly:
  `/Users/kurtn/Library/Caches/pypoetry/virtualenvs/pablo-YtzK5q4a-py3.13/bin/python`.
- **The two reviewers disagreed with the current tree.** Codex reported two
  findings as "still live" that `8664db9` had already fixed (the schema fence,
  the `exc_info` removal). Verify every finding against the actual tree before
  acting — one was a genuine live bypass, two were stale.

## Filed, not fixed

**`THERAPY-o0nz8` (P1)** — single-practice deployments run with **no RLS at
all**. Verified: the `practice` schema has 16 tables, 0 with `relrowsecurity`,
0 policies. `enable_rls_on_schema` returns early for `DEFAULT_PRACTICE_SCHEMA`
(calling it "the template schema"), but `provisioning.py:156` also provisions it
as the *live* schema when `multi_tenancy_enabled=False`, the shipped default.

Scope: Pablo dev/prod are **not** affected — the SaaS overlay sets
`MULTI_TENANCY_ENABLED=true`, so data lives in `practice_*` schemas that do get
policies. OSS self-hosters on the default are affected.

Not an open door — the repositories carry explicit `_has_patient_access` /
`user_id ==` checks, so the app layer holds. What is missing is the backstop,
in the deployment least likely to have code review. The bead has three options
weighed; the template-regeneration interaction is the risk.

## What is next, in order

1. **Resolve the hang** (above), then push `b57e156` and let CI confirm.
2. `pablo#772` merges once that is green. Kurt has already approved it.
3. **`u37i.2`** — magic-link issuance + step-up redemption. Note the SaaS
   companion auth core is already merged at `backend/saas/patient_companion/`,
   so the OSS side is the thinner half. When you write the first real resolver,
   read `PatientPrincipalResolver`'s docstring first: return `None` to reject,
   and whatever you mint must be structurally unacceptable to every clinician
   verifier.
4. **`u37i.4`** — patient-principal audit. Remember the finding from the
   previous handoff: `audit_logs` is force-RLS'd on `user_id`, so every
   patient-principal audit INSERT is denied today. `.4` must change the
   `audit_logs` policy itself; `actor_type` (`84f9fb0`) was the prerequisite.
   Land order is `.3` → `.4`; `.3` is in `#772`.

## Open questions nobody has answered

- Should `AuthStrength` be enforced rather than merely recorded? Today routes
  are trusted to check it. A `require_stepped_up` dependency before the first
  clinical patient route would make it opt-out instead of opt-in.
- **WebSocket transport.** `BaseHTTPMiddleware` does not run for the websocket
  scope, so there is no session, no stash and no guard — and `get_db_session()`
  raises a 500 rather than a 401. Companion chat in this stack is WebSocket-
  based. This needs deciding *before* the resolver PR, not after.
- Should the process-wide resolver registry be frozen after bootstrap? It has a
  public `clear()` and no dedupe.
