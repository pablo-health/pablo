# Authenticated Principals — OSS Design Doc

**Status:** Living. Reflects HEAD as of 2026-08-27 (`54db955`).
**Shipped:** the patient principal, the resolver seam, `app.current_patient_id`,
and patient-scoped row policies with `patients` registered read-only.
**Not shipped:** any resolver. The seam has no front door in this repository yet,
so no request can currently resolve to a patient principal.

This file is the canonical description of how Pablo decides *who is calling* and
what that answer is allowed to reach. The enforcement lives in docstrings beside
the code that enforces it — `app/auth/patient_context.py`, `app/db/__init__.py`,
`app/db/middleware.py` — and those remain the contract. This document is the map:
it says how the pieces fit and why they are shaped the way they are. Code that
diverges from this doc should be treated as a bug in the code or an amendment
owed to the doc, not as "the code is right, the doc is stale."

---

## §1. Goals and non-goals

### §1.1 Goals

1. Two authenticated identities that cannot be mistaken for one another: the
   clinician, and the patient.
2. Isolation that holds at the database, not only in the request handler, so a
   forgotten predicate is a bug rather than a disclosure.
3. One extension point for patient authentication, so a second way of proving
   identity is an adapter rather than a rewrite of every route.
4. Failure modes that close rather than open, and that are loud where silence
   would be indistinguishable from "no data".

### §1.2 Non-goals

1. **Authorization beyond the principal.** Deciding that a clinician may perform
   an action is a separate concern; this layer decides only who they are and
   which rows the database will show them.
2. **A patient credential format.** The seam takes an opaque credential. Nothing
   here names an identity provider, a token shape, or a vendor — see §5.
3. **Column-level restriction.** Row-level security is exactly that. See §9.2.

---

## §2. The two principals

Pablo's original principal is the clinician: an authenticated subject resolved to
a user id and a practice, carried as `TenantContext`
(`backend/app/auth/service.py:54`) and produced by `get_tenant_context`
(`backend/app/auth/service.py:406`).

Patient-facing surfaces need an identity that is *not* that. A patient
authenticates to their own record inside one practice and must never acquire a
clinician's reach across the rest of it. That principal is `PatientContext`
(`backend/app/auth/patient_context.py:90`).

```
PatientContext
  patient_id       who is calling
  practice_schema  which practice's schema their record lives in
  credential_kind  which front door they came through
  auth_strength    how strongly they proved it
```

### §2.1 They are deliberately unrelated types

`PatientContext` is **not** a subclass of `TenantContext`, and this is load
bearing rather than stylistic. A shared base class is precisely how a patient
principal would one day satisfy a dependency that meant to ask for a clinician —
the substitution would typecheck, the route would read a `user_id` attribute that
happened to exist, and nothing would fail. The two principals share no
substitutable behaviour, so they share no type.

The same reasoning applies in reverse. A patient credential must be
*structurally* unacceptable to the clinician verifiers, not merely different in
practice. That is a requirement on whoever writes a resolver, and it is stated as
one — see §5.2.

### §2.2 Auth strength is recorded, not enforced

`AuthStrength` (`backend/app/auth/patient_context.py:71`) distinguishes
`SINGLE_FACTOR` — possession of one thing, such as an unredeemed link that
arrived by email — from `STEPPED_UP`, where a second factor was cleared. A link
forwarded to the wrong person is a single factor in someone else's hands, so
surfaces exposing clinical content should require `STEPPED_UP`.

As of this commit the dependency records the strength and each route decides what
it needs. Whether that should become a dependency routes opt *out* of, rather
than one they opt in to, is an open design question.

---

## §3. Two layers of isolation

Isolation is enforced twice, at different granularities, and the layers catch
different mistakes.

**Layer 1 — the schema.** Each practice's data lives in its own PostgreSQL
schema, and a request's `search_path` selects it. `set_tenant_schema`
(`backend/app/db/__init__.py:380`) validates the name as an identifier before it
reaches SQL, so a schema name that came from a credential cannot inject. This
layer separates practices from each other.

**Layer 2 — the row.** Within a schema, row-level security decides which rows a
principal sees. Policies read a transaction-local GUC naming the caller, and
`enable_rls_on_schema` (`backend/app/db/__init__.py:980`) applies them per
schema. This layer separates principals from each other inside one practice.

Layer 2 is what makes an IDOR a non-event rather than a disclosure: two patients
in the same practice share a schema, a `search_path`, and a connection pool, so
the schema boundary cannot help. Only the row predicate separates them.

### §3.1 Two GUCs, not one

The clinician GUC is `app.current_user_id`; the patient GUC is
`app.current_patient_id`. They are separate variables, backed by separate
ContextVars (`backend/app/db/__init__.py:61` and `:80`).

A single "whoever is calling" GUC is the obvious simplification and it is wrong
twice over. A policy written `USING (patient_id = current_setting(...))` would
accept a clinician whose user id happened to equal a patient id. And every
existing clinician policy — all of which key on that one variable — would become
satisfiable by a patient principal. Two variables let a policy say *which kind*
of principal it grants to. There is a test for exactly that collision.

### §3.2 The predicate idiom

Every policy compares a cast column against the raw GUC:

```sql
patient_id::text = current_setting('app.current_patient_id', true)
```

The column is cast, not the GUC. `current_setting(..., true)` returns `NULL` when
unset, so an unarmed transaction yields `NULL` and matches no row — fail-closed —
and there is no `invalid input syntax for uuid` path that would let a caller
distinguish states from the error. A cleared GUC is the empty string, which
matches nothing for the same reason. See `_patient_principal_predicate`
(`backend/app/db/__init__.py:911`).

---

## §4. The invariant: exactly one principal per transaction

**A transaction carries the clinician GUC or the patient GUC. Never both.**

This is the single most important property in this document, and the reason is
PostgreSQL's policy algebra: permissive policies are **OR**ed. A transaction with
both variables armed satisfies the clinician policies *and* the patient policies
at once, and sees the union of what each would grant. That union is precisely the
reach the two-principal split exists to prevent.

The invariant is maintained structurally rather than by convention, in three
places:

1. **Arming one principal disarms the other.** `arm_current_user_id`
   (`backend/app/db/__init__.py:414`) and `arm_current_patient_id` (`:533`) each
   end by calling `_disarm_other_principal` (`:470`), which clears the other
   principal's `Session.info` key, its ContextVar, and the GUC itself. The
   clearing statement is issued unconditionally. Skipping it when no in-process
   carrier is set looks like a safe optimisation and is not: it is a claim about
   every present and future writer of those variables, and a connection carrying
   a session-level value survives its return to the pool.

2. **Every new transaction re-establishes it.** `set_config(..., is_local=true)`
   is transaction-scoped, so a mid-request commit clears it. The `after_begin`
   listener `_rearm_rls_principal_gucs_on_txn_begin`
   (`backend/app/db/__init__.py:567`) re-arms the principal that is set **and
   blanks the one that is not**, in a single statement. Blanking matters as much
   as arming: without it, the listener would restore the patient GUC after a
   commit and leave the clinician GUC at whatever the connection was carrying.
   The statement costs no extra round trip — it replaces the one that was already
   being issued.

3. **Both at once is refused.** If the listener ever sees both principals
   available, it raises. Arming both is the union; arming neither is a silent
   zero-row request, which reads as "no data" rather than as a bug and is the
   failure mode this codebase has been bitten by before. The state has no
   legitimate producer, so naming it loudly beats guessing.

### §4.1 Why the arming rides two carriers

Each armed id is stored on `Session.info` *and* in a ContextVar
(`_RLS_USER_ID_KEY`, `_RLS_PATIENT_ID_KEY` — `backend/app/db/__init__.py:69`,
`:85`). The Session object travels by reference across the threadpool workers
that run a synchronous route's dependency and its endpoint; a ContextVar set
inside one of those workers is discarded when it returns. `Session.info` is
therefore the source of truth and the ContextVar is the fallback for code paths
that have no session in hand.

The tenant schema has no such second carrier, which is why
`get_patient_context` must remain `async` — see §9.1.

---

## §5. The resolver seam

Patient authentication is an extension point, not a fixed implementation. One
principal, many possible front doors: a mailed link with a second-factor step-up,
an embedded widget presenting a host-signed assertion, an enterprise SSO
assertion, a standards-based launch context. Each is an adapter.

Nothing in the seam names a provider or a token type. A resolver receives a
`PatientCredential` (`backend/app/auth/patient_context.py:124`) — a `kind`, an
opaque `value`, and a `parameters` mapping for transport extras — and returns a
`PatientContext` or `None`. It never sees an HTTP request, so it can be exercised
without a web layer and cannot quietly reach for some other part of the request
to make its decision.

`PatientResolverRegistry` (`:182`) holds resolvers keyed by credential kind.
Several may share a kind; registration order is precedence order, and the first
to claim a credential wins.

### §5.1 A raising resolver aborts resolution

`None` means "not my credential". An exception means "I could not decide", and it
stops resolution entirely rather than falling through to the next resolver.

That asymmetry is deliberate. Letting a could-not-decide fall through converts it
into "someone else may decide", which is an auth-strength downgrade: register a
strong front door ahead of a weaker one — the arrangement `AuthStrength`
explicitly anticipates — and anyone who can make the strong one raise is served
by the weak one instead. Aborting costs a resolvable credential nothing that
matters. The request fails closed, the caller gets the same uniform 401 as every
other failure, and no information about which door broke reaches them.

Only the exception's *type name* is logged — never its message or traceback.
Identity libraries routinely put the offending credential into exception text, so
a traceback here would be a credential-to-logs channel.

### §5.2 The contract a resolver owes

1. **Return `None` to reject.** Raise only for genuine infrastructure failure.
2. **Whatever you mint must fail every clinician verifier.** The reverse
   separation direction currently holds only because no patient credential format
   exists. A future front door minting something a clinician verifier accepts
   would have that credential treated as a user. This must be true structurally,
   not incidentally.
3. **Do not block the event loop.** `get_patient_context` is async; a resolver
   doing network I/O should not do it synchronously on the calling thread.

---

## §6. The clinician guard

Both principals can arrive over the same transport, so the resolver seam would
otherwise be one forgetful adapter away from letting a clinician's credential
resolve to whichever patient it happened to mention.

`DatabaseSessionMiddleware` (`backend/app/db/middleware.py:150`) verifies every
inbound `Authorization` credential and, when it belongs to a clinician, caches
the identity on the request. `get_patient_context`
(`backend/app/auth/patient_context.py:304`) refuses outright when it finds one.
The clinician dependencies reuse the same cached identity, so verifying here
moves work rather than adding it.

Two properties of that guard matter more than they look:

**It runs unconditionally.** Not gated on deployment shape or configuration. A
guard that is only armed in some configurations is a guard that is absent in the
others, and the others will not be the ones under review.

**Its parse must match the seam's parse exactly.** `_verify_and_stash_clinician_identity`
(`backend/app/db/middleware.py:37`) ignores the authentication scheme entirely,
because `_credential_from_request` (`backend/app/auth/patient_context.py:260`)
builds a credential out of *whatever* scheme it finds and keys the registry on
it. Any narrowing here — a scheme allow-list, a case-sensitive comparison —
creates a hole shaped exactly like the difference between the two parses, and the
credential falls through it to the resolvers. This has been got wrong twice, in
two different shapes, which is the argument for the two parses being one parse.

---

## §7. What a patient may reach

Patient-readable tables are an explicit registry, not an inference:

```python
PATIENT_READABLE_TABLES = {"patients": "id"}   # backend/app/db/__init__.py:861
PATIENT_WRITABLE_TABLES = {}                   # :880
```

**Column shape implies nothing.** Plenty of tables carry a `patient_id` without
the patient being entitled to read them — `notes` is the clinician's clinical
record *about* a patient, not a record *for* them. Patient-readability is a
product decision and it is written down as one. `register_overlay_patient_scoped`
(`:883`) is how a deployment adds its own.

Read and write are separate registries so that granting a read never silently
grants a write.

The policies are **additive**. A registered table gets a patient arm alongside its
clinician policy; because permissive policies OR, the patient arm widens access
for a patient principal without altering — or even textually touching — the
clinician policy beside it. An unregistered table gets nothing, and therefore
fails closed for a patient principal automatically: its policy keys on the
clinician GUC, which a patient request never arms.

A registration naming a column the table does not have raises during
provisioning rather than shipping a policy that matches nothing, and a
registration the policy loop never visits raises too — a grant that silently
fails to exist is worse than one that fails loudly.

---

## §8. Route shape is the primary control

**Scope every query by `patient_id` from the context. Do not accept a patient id
from the client.**

A route shaped `GET /patient/record/{patient_id}` has an IDOR surface by
construction. It invites an id the caller controls, and then the only thing
between two patients is that somebody remembered to compare it. A route that
reads the id from the principal has nothing to compare and nothing to forget —
the credential already says who is calling.

Row-level security backs this up; it does not replace it. Defence in depth means
both, and the ordering matters: the route shape is the control, and the row
policy is the thing that holds when a control is got wrong.

---

## §9. Boundaries

### §9.1 `get_patient_context` is `async`, and must stay that way

FastAPI runs a *synchronous* dependency in a throwaway threadpool worker whose
context is a copy, so a `ContextVar.set()` inside one is discarded the moment it
returns. The dependency arms the tenant schema through exactly such a set, and
the pool-checkout listener re-reads that variable on every connection the request
later acquires.

As a sync dependency the schema would therefore survive only until the first
mid-request commit released the connection — after which the next checkout would
stamp whatever the middleware left, while the patient GUC stayed correctly armed
off `Session.info`. The remainder of the request would read and write the wrong
schema under a live patient identity. A regression test fails if the `async` is
removed.

### §9.2 Row-level security has no column granularity

A patient-scoped policy grants the whole row. A table may hold columns written
for clinical staff rather than for the patient, and the database cannot
distinguish them. A patient-facing route must therefore select named columns, or
serialize through a response model that names them. It must never return an ORM
model directly.

### §9.3 The dependency is HTTP-only

`get_patient_context` takes a `Request`, and `DatabaseSessionMiddleware` is HTTP
middleware that does not run for the WebSocket scope. On a WebSocket there is no
request-scoped session, no cached identity for the guard to read, and no arming.
A patient WebSocket surface needs a parallel entry point that rebuilds *both* the
guard and the arming for that scope. It must not reach for this one.

---

## §10. Proving the isolation

Four suites, deliberately not redundant, ordered by how much of the real stack
they exercise:

| Suite | What it proves |
|---|---|
| `backend/tests/test_patient_context.py` | seam semantics: registry precedence, abort-on-raise, credential normalization |
| `tests_integration/database/test_patient_guc_integration.py` | GUC mechanics against real PostgreSQL |
| `tests_integration/database/test_patient_principal_rls.py` | two-patient isolation by direct SQL on a real provisioned schema, with the shipped policies |
| `tests_integration/database/test_patient_idor_http.py` | the same question over real HTTP with nothing mocked |

**The unit layer cannot prove isolation, by construction.** Its dependency tests
monkeypatch the database-arming path — which is the part that has been wrong.
Security coverage added there is not coverage.

**Mutation-test what you add.** Every control described here was verified by
breaking it deliberately and confirming the suites noticed: replacing a real
predicate with `USING (true)`, unregistering a table, making the dependency
synchronous. A control whose test still passes when the control is removed is not
a control.

The IDOR suite deliberately includes a route with **no** ownership check, so that
row-level security is the only thing under test — a route that also checked would
pass even if every policy were broken, because it would be testing its own `if`
statement rather than the database. Those routes are adversarial fixtures and are
not a pattern to copy.
