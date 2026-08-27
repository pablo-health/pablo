# The patient principal (pablo#772) — state as of 2026-08-27

Picks up from `pablo-saas/docs/internal/handoff-2026-08-26-consent-and-fleet.md`,
which is still accurate about the consent design, the fleet and the traps. This
covers only the patient-principal work.

## Status

`pablo#772` is `u37i.1` (the patient principal) plus `u37i.3` (patient-scoped
RLS policies). Kurt asked for `.3` in the same PR after asking "I feel actually
testing that this works on our first table is important" — the right call, and
`.3` registers exactly one core table so it fits.

The branch is verified locally: unit suite 2634 passed, `tests_integration/`
288 passed / 6 skipped. The one remaining integration failure,
`test_fastapi_dependency_cache_release`, is environmental — it asserts that the
installed fastapi still exhibits the dependency-cache leak it guards against,
and the local venv carries fastapi 0.138.1 while `pyproject.toml` requires
`>=0.139.0`. It fails identically on `origin/main`. CI installs from the lock
and is unaffected.

**Nothing here is reachable yet.** No resolver is registered, no route depends
on `get_patient_context`, and `PATIENT_READABLE_TABLES` seeds exactly one entry.
The seam is inert until `u37i.2` lands a front door. That is what makes the
remaining gaps below "close before the door opens" rather than "incident".

## Design decisions worth not relitigating

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
- **`get_patient_context` must stay `async`.** Load-bearing, not style. A sync
  dependency runs in a throwaway threadpool worker whose context copy is
  discarded on return, so its `set_tenant_schema` ContextVar write is lost; the
  GUC survives that hop because it also rides `Session.info`, and the schema has
  no such carrier. After the first mid-request commit the next checkout would
  re-stamp `search_path` from `DEFAULT_PRACTICE_SCHEMA` while the patient GUC
  stayed correctly armed — the rest of the request reading and writing the
  template schema under a live patient identity. A regression test fails if
  anyone reverts it to `def`.

## The bug class that keeps recurring: two principals on one transaction

Four separate defects in this PR were the same shape. The patient policies are
**permissive**, so Postgres ORs them with the clinician policies: a transaction
carrying both GUCs satisfies both families and sees the UNION of clinician and
patient grants. Every control here exists to make "exactly one principal per
transaction" true by construction, and each defect was a place where it was
true only by assumption instead.

**1. The clinician guard was dead code.** `get_patient_context` refuses a
credential the middleware already verified as a clinician's — but the
verify-and-stash step sat inside `if settings.multi_tenancy_enabled:`, which
defaults to `False`. On a single-tenant install — the default, and what a
self-hosted companion runs — nothing ever set the value and the guard never
fired.

**2. The stash was case-sensitive.** It matched `"Bearer "` while FastAPI's
`HTTPBearer` compares `scheme.lower()`. So `Authorization: bearer <token>`
authenticates fine on clinician routes but skipped the stash — one lowercase
letter and a clinician's token reached every patient resolver. The parse in
`_verify_and_stash_clinician_identity` must stay at least as permissive as
`HTTPBearer`'s, because the guard is only as good as that parse.

**2b. The stash only looked at `bearer`, but the seam takes any scheme.**
`_credential_from_request` builds a `PatientCredential` out of *whatever*
scheme it finds and keys the registry on it — deliberately, so a future
non-bearer front door can register its own kind. The stash checking for
`bearer` therefore covered exactly one of those kinds: a clinician's token sent
as `Authorization: Token <jwt>` skipped the stash and would be handed to
whichever resolver registers under `"token"`. Same hole as 2, one level up, and
it reappears every time the two parses disagree. The middleware now ignores the
scheme entirely and mirrors `_credential_from_request`'s parse exactly; a
non-clinician credential simply fails to verify and stashes nothing.

**2c. "No stash" conflates a rejection with a failure — still open, and the
obvious fix is wrong.** The middleware swallows every verifier exception, so an
identity-provider outage looks identical to "every verifier decided this is not
a clinician's token". `verify_id_token` runs `check_revoked=True`, a network
round trip, so the outage case is real and arrives holding a credential that may
well be a clinician's.

The tempting fix is a `clinician_verification_errored` marker that
`get_patient_context` also refuses on — mirroring
`PatientResolverRegistry.resolve`'s rule one layer down, and keyed off
`VerifierRegistry.verify`'s existing line (a 401 means "not my token" and falls
through; anything else propagates). **It was tried and reverted.**
`verify_firebase_token` calls `initialize_firebase_app()` *outside* its `try`,
so a deployment with no Firebase credentials raises on **every** request,
permanently — the marker made patient authentication silently impossible rather
than merely refusing it during an outage. CI caught it as 15 patient tests
returning 401; it passed locally, because a developer machine has ADC and so
gets a clean 401 from a real verifier. **That asymmetry is worth remembering:
this particular fix is untestable in the failing direction on a machine with
credentials.**

Distinguishing "this verifier is not configured" from "this verifier broke
mid-flight" belongs in the verifier layer as a typed error, not in
exception-shape guessing at the middleware. Until then the resolver contract in
`PatientPrincipalResolver` carries it, and the reasoning is recorded in
`_verify_and_stash_clinician_identity`'s docstring so the next person does not
re-derive the same wrong fix.

**3. `_disarm_other_principal` skipped the clearing statement** when neither
in-process carrier was set, reasoning that a transaction-local GUC cannot be
armed if this process did not arm it. True of every call site in `app/` today —
and it is a claim about every present and future writer of those two GUCs,
restated as an optimisation. A connection carrying a session-level
`set_config(..., false)` survives being returned to the pool, and the skip waved
it straight into a patient request. Now unconditional.

**4. The clear did not survive a commit.** `set_config(..., is_local=true)` dies
with its transaction, and Pablo commits mid-request (`_commit_intermediate`'s
lock release before the SOAP call). After that commit the `after_begin` listener
re-armed the patient GUC and left the clinician one at whatever the connection
was carrying — the union returned one commit later. The listener now sets both
GUCs in one statement: arm the principal that is set, blank the other. Same
round-trip count as before, and the property now holds on every transaction
rather than only the first.

3 and 4 were found by the integration suite once it could actually run to
completion (see below) — a clinician UUID from an unrelated test module
surfacing on a patient request. Neither was reachable in production: nothing in
`app/` sets these GUCs session-level, and there are no patient routes. They were
latent holes in the control this PR exists to build.

The listener now also **raises** when both principals are visible at once,
rather than arming both or arming neither. The arming functions make that state
unreachable, but they clear carriers on the `Session` they are handed while the
listener also reads the ambient ContextVars — so a caller entering
`tenant_db_session` inline on the event loop, against its documented
worker-thread contract, would present both. Arming both is the union; arming
neither is a silent zero-row request, which reads as "no data" rather than as a
bug. The state has no legitimate producer, so a 500 naming it beats either
guess.

## The suite hang — resolved

The full `tests_integration/` suite used to park for tens of minutes on
`test_audit_writes.py`, with a Postgres connection sitting `idle in transaction`
on an `INSERT INTO audit_logs` and `pg_blocking_pids` reporting nothing blocked.

It was the **Cloud Logging audit dual-write**. `audit_dual_write_enabled`
defaults to `True`; `AuditService._persist` therefore calls
`write_to_cloud_logging`, which builds a real client and issues a real network
write. `tests/conftest.py:26` disables the flag for the unit suite and documents
exactly this failure mode; `tests_integration/conftest.py` never got the same
line. CI has no Application Default Credentials, so the client constructor
raises immediately and the miss is invisible there — on a developer machine with
ADC it blocks inside `google.cloud.logging_v2.logger._do_log`.

Two consequences, both now fixed by one line in the integration conftest:

- The suite completes in ~80 seconds instead of never.
- Synthetic audit rows are no longer written into the `pablo.audit_events`
  stream that the retention-locked GCS sink mirrors for six years.

The diagnosis in the previous version of this document — that the hang was
`_disarm_other_principal` holding the audit writer's transaction open — was
wrong, and the early-return guard added to "fix" it was the defect described as
3 above. `arm_current_user_id` already executes a `set_config` of its own, so a
second statement changes no transaction state.

`faulthandler` is what settled it: `PYTHONFAULTHANDLER=1`, then
`kill -ABRT <pid>` on the parked process dumps every thread's Python stack.
Three hypotheses had been chased without one.

## Testing, and what each layer is for

Four suites, deliberately not redundant:

- `backend/tests/test_patient_context.py` — units. **Its dependency tests
  monkeypatch the DB-arming path**, which is precisely why they could not catch
  defects 3 and 4. Do not add security coverage here and think you are done.
- `tests_integration/database/test_patient_guc_integration.py` — GUC mechanics
  against real Postgres.
- `tests_integration/database/test_patient_principal_rls.py` — two-patient
  isolation via direct SQL on a **real provisioned schema** with the real
  shipped policies.
- `tests_integration/database/test_patient_idor_http.py` — the same question
  through **real HTTP with nothing mocked**.

**The IDOR file's design, since it looks wrong at a glance.** It has two route
shapes. `/patient/record/{patient_id}` deliberately has *no ownership check*, so
RLS is the only control under test — a route that also checked would pass even
if every policy were broken. `/patient/me/record` is the shape real routes
should copy: it takes no id from the client at all and scopes by
`context.patient_id`, so there is no ownership question to get wrong. **Do not
copy the `{patient_id}` routes into production.**

**Mutation-test anything you add here.** Everything above was mutation-tested:

| Mutation | Result |
|---|---|
| Real predicate → `USING (true)` | 10 fail |
| `patients` unregistered | 5 fail |
| Canary policy → `USING (true)` | 8 fail |
| `get_patient_context` → `def` | 4 fail |

A test that asserts a *pool* behaviour it cannot control is order-dependent, not
strict. `test_guc_is_absent_on_a_session_that_armed_nothing` used to arm a
pooled session and assert `pg_backend_pid()` matched on the next checkout — an
honest anti-vacuous guard, since a fresh backend carries no GUC and would pass
trivially, but the pool makes no such promise and a full-suite run failed the
test rather than the product. It now owns one connection and binds both sessions
to it, which is strictly stronger.

## Traps

- **Running `tests/` and `tests_integration/` in one pytest invocation silently
  disables the integration suite.** `tests/conftest.py:28` plants a dummy
  `DATABASE_URL` at import time; `tests_integration/conftest.py`'s
  `pytest_configure` sees it, assumes a real database, returns early and never
  sets `DATABASE_BACKEND=postgres` — so every integration module skips. Exit 0,
  looks like a pass. Argument order does not help. **`make test-all` does
  exactly this**, so that target reports success while testing half of what it
  claims. Run them separately. Worth fixing in the conftest.
- **Killed runs leave orphaned testcontainers.** This conftest sets
  `TESTCONTAINERS_RYUK_DISABLED=true`, so nothing reaps them; they accumulate
  silently and degrade everything afterwards. `docker ps` and remove the stray
  `postgres:16-alpine` after any kill. Leave `pablo-saas-*`, `pablo-postgres-1`,
  `dramellea-*` and `buildx_*` alone — those are Kurt's, not test containers.
- **`poetry run` in an OSS worktree makes an empty in-project `.venv`** and
  subprocess-based tests (`alembic upgrade head`) then resolve to system Python
  3.10 and fail with `'type' object is not subscriptable`. Either
  `poetry install` in the worktree or use the main venv directly:
  `/Users/kurtn/Library/Caches/pypoetry/virtualenvs/pablo-YtzK5q4a-py3.13/bin/python`.
- **Reviewers disagree with the tree.** Codex reported two findings as "still
  live" that `8664db9` had already fixed. Verify every finding against the
  actual tree before acting — one of the three was a genuine live bypass, two
  were stale.

## Open, and tracked

**A patient principal can INSERT into `patients`.** The clinician arm splits the
`patients` policy per command to fix an INSERT chicken-and-egg, and the INSERT
half is `WITH CHECK (true)` — it consults no GUC, so it admits any principal
subject to RLS. SELECT/UPDATE/DELETE are all correctly closed to a patient. Not
reachable while no patient route exists; must close before `u37i.2`.

**A patient can never authenticate on a single-tenant install, and the code
argues both sides.** `_is_tenant_schema` requires a `practice_*` schema, so a
single-practice deployment — whose data lives in `practice` — produces the
uniform 401 for every resolver it could honestly register. Meanwhile the
middleware's own docstring justifies unconditional clinician verification
precisely because single-tenant "is the configuration a self-hosted patient
companion would run in". Both cannot be true. It fails closed, so this is a
design contradiction rather than a hole, but it needs deciding before `u37i.2`:
either single-tenant patient surfaces are unsupported and the middleware comment
should stop implying otherwise, or the dependency allows `DEFAULT_PRACTICE_SCHEMA`
with the RLS backstop knowingly absent — which `PatientContext`'s docstring
already anticipates, and which is the same hole as the next item.

**Single-practice deployments run with no RLS at all.** The `practice` schema
has 16 tables, 0 with `relrowsecurity`, 0 policies. `enable_rls_on_schema`
returns early for `DEFAULT_PRACTICE_SCHEMA` (calling it "the template schema"),
but `provisioning.py:156` also provisions it as the *live* schema when
`multi_tenancy_enabled=False`, the shipped default. Pablo dev/prod are not
affected. Self-hosters on the default are. Not an open door — the repositories
carry explicit `_has_patient_access` / `user_id ==` checks, so the app layer
holds; what is missing is the backstop, in the deployment least likely to have
code review.

**The Cloud Logging dual-write is unverified end to end and fails silently.**
`write_to_cloud_logging` swallows every exception into a warning nobody reads,
and the only coverage monkeypatches the function itself. An absence alert on the
`pablo.audit_events` stream is worth more than any test here.

**Only `tenant_db_session` and `run_in_tenant` clear the patient ContextVar.** A
patient route that reached for `create_standalone_session` directly, or a
Starlette background task that opened a session outside those two primitives,
would inherit the armed patient id through the listener's ContextVar fallback.
That mirrors the pre-existing clinician behaviour exactly, and there is no
current trigger — but the first patient route is where it stops being
theoretical, so open the unit of work through the primitives.

**Column scope.** `rls_patient_self_read` grants a patient their whole
`patients` row, and RLS has no column granularity. That row carries `diagnosis`,
`sliding_scale_note` ("in the clinician's own words", written for staff),
`rate_cents`, `chart_closure_reason` and `origin`. A patient route must project
explicit columns; it must never serialize the ORM model.

## What is next, in order

1. **`u37i.2`** — magic-link issuance + step-up redemption. The SaaS companion
   auth core is already merged, so the OSS side is the thinner half. When you
   write the first real resolver, read `PatientPrincipalResolver`'s docstring
   first: return `None` to reject, and whatever you mint must be structurally
   unacceptable to every clinician verifier.
2. **`u37i.4`** — patient-principal audit, and it gates `u37i.2` rather than
   following it. `audit_logs` is force-RLS'd on `user_id`, so every
   patient-principal audit INSERT is denied today: a patient route would either
   500 on its own audit write or, if someone "fixed" that by not auditing, read
   PHI with no entry in the audit-of-record. `.4` must change the `audit_logs`
   policy itself; `actor_type` (`84f9fb0`) was the prerequisite. Land order is
   `.3` → `.4` → `.2`.

## Open questions nobody has answered

- Should `AuthStrength` be enforced rather than merely recorded? Today routes
  are trusted to check it. A `require_stepped_up` dependency before the first
  clinical patient route would make it opt-out instead of opt-in.
- **WebSocket transport.** `BaseHTTPMiddleware` does not run for the websocket
  scope, so there is no session, no stash and no guard — and `get_db_session()`
  raises a 500 rather than a 401. OSS has no websocket routes today; companion
  chat in this stack is websocket-based. This needs deciding *before* the
  resolver PR, not after.
- Should the process-wide resolver registry be frozen after bootstrap? It has a
  public `clear()` and no dedupe.
