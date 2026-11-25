# CLAUDE.md — backend/app/db/

Schema and migration guardrail from the root [`CLAUDE.md`](../../../CLAUDE.md),
kept here so it loads when you work on the models, the RLS policies or
provisioning. The number matches the root list, which keeps a one-line
summary that covers the alembic chains too.

## Guardrails (load-bearing, don't skip)

4. **Models and migrations ship together.** Every change to a
   SQLAlchemy model in `backend/app/db/models.py` (or
   `platform_models.py`) must include a same-commit Alembic migration.
   Don't land a model change and "add the migration later" — that
   boots a broken dev env for everyone else.

   **Two chains, and the model tells you which one.** They are separate
   graphs with separate bookkeeping, and a revision belongs to exactly
   one:

   | Model | Chain | Version table | Run with |
   |---|---|---|---|
   | `models.py` (per-tenant) | `backend/alembic/versions/` | `<practice_*>.alembic_version` | `alembic upgrade head` |
   | `platform_models.py` (shared) | `backend/alembic_platform/versions/` | `platform.alembic_version_platform` | `alembic -n platform upgrade head` |

   Put platform DDL in the platform chain, **not** the tenant chain.
   The tenant chain is fanned out once per practice schema, so anything
   platform-shaped in it executes once per tenant and has to be
   hand-written idempotent to survive that; and because a new tenant is
   built from the template and stamped rather than migrated, the chain
   is not what builds anything on a fresh provision. 35 tenant-chain
   revisions touch `platform.` for historical reasons — that is the
   problem being unwound, not the pattern to copy (`PABLO-k7it`).

   **Same commit, regenerate the template for whichever chain you
   touched.** Both schemas are built from a captured template and
   evolved by their chain:

   ```
   poetry run python backend/scripts/regen_tenant_template.py    # backend/alembic/
   poetry run python backend/scripts/regen_platform_schema.py    # backend/alembic_platform/
   ```

   The template is the canonical schema applied to every freshly-
   provisioned tenant — `create_practice_schema` reads it directly
   (it does **not** run alembic per tenant, by design). DDL outside
   the ORM (functions, triggers, custom types, raw `op.execute(...)`
   SQL) is invisible to `Base.metadata.create_all`; if you forget to
   regenerate, new tenants land at HEAD-stamped but **missing your
   DDL**, and every code path that touches it 500s. This is exactly
   how patient-create regressed on 2026-05-17. CI enforces this for
   both templates: the `Tenant template matches alembic head` job
   regenerates each one and fails on any diff.

   The platform template is the same contract for the shared schema —
   it carries the row policy, trigger, function, CHECK constraints and
   partial indexes that no ORM model expresses, which is exactly why it
   is a capture rather than something generated from the models. A
   platform revision that lands without a regen leaves a fresh install
   short of whatever it added.

   **Nothing builds a schema from ORM metadata any more, and the order
   is load-bearing.** `create_all` used to run at boot and in the tenant
   chain's `env.py`; both are gone. So:

   - **The migrate job builds and provisions schemas; boot touches no
     database.** `python backend/bin/migrate.py` runs both chains in
     order, then `ensure_schemas`: the template, and on an empty database
     the deployment's own practice. It refuses if the platform schema is
     missing. Importing `app.main` must not connect — a slow first
     connection on a new instance crashed the import while it did.
   - **The platform chain runs before the tenant chain**, always. Tenant
     revisions declare foreign keys into `platform.users` and create
     nothing in that schema, so the tenant chain cannot run first — it
     fails with `PlatformSchemaMissingError` naming the fix. That
     ordering is explicit at every call site (`bin/migrate.py`, the
     Makefile, both regen scripts, the test fixtures) rather than hidden
     in `env.py`, because a nested `command.upgrade` inside an alembic
     `env.py` tears down alembic's module-level proxies and dies with
     `KeyError: 'config'`.
   - **Platform DDL belongs in the platform chain**, not in a tenant
     revision. A tenant revision touching `platform.` is re-run once per
     practice schema, so it must be idempotent by hand, and it runs
     *after* the platform chain — so it can silently undo platform-side
     work on every fresh install. That is where 15 duplicate indexes
     came from, and why the revision that drops them has to sit at the
     end of the tenant chain rather than in the platform chain: the
     eight revisions that create them are there, and they run second.
     35 tenant revisions still touch `platform.` for historical reasons.
     Don't add the 36th (`PABLO-k7it`).

   **RLS enforcement** — every per-tenant table that carries a
   `user_id`, `patient_id`, or `id` column is force-RLS'd by
   `enable_rls_on_schema`. A newly-added table MUST have a policy
   branch in the planner it calls (`rls_inference/table.py`, which takes
   the registries as arguments and reads no module state) or be
   explicitly listed as not-row-scoped via
   `register_overlay_not_row_scoped()`, otherwise it silently ships as
   deny-all.

   The tenant template carries that function's output: its last section
   (after the row-level security marker) is captured by running the real
   `enable_rls_on_schema` over a scratch practice during regen, and
   provisioning applies it to every new practice. `enable_rls_on_schema`
   still runs afterwards as a verify — it rebuilds only tables whose flags,
   policies or policy fingerprints differ, so it stays the repair pass for
   existing practices. **So a change to a policy in that function is a
   template change too: regenerate the template in the same commit**, or
   the `Tenant template matches alembic head` job fails.

   The same section carries the practice directory's access: its three
   read policies, captured like the rest, and the grants, pinned search
   paths and function ownership, written out by
   `practice_directory.directory_access_sql`. Provisioning runs nothing
   else for it. So a change to what a directory function reads is a
   template change too, **and** needs a revision that calls
   `apply_practice_directory_access`, because that is the only way a
   practice that already exists gets it.

   That isolation contract is verified at **three layers**:

   - **L2 real-time hook** — `.claude/settings.json` PostToolUse hook runs
     `poetry run python .claude/skills/rls-coverage-check/check.py`
     automatically after any Edit/Write to `backend/app/db/models.py`.
   - **L3 unit guard (CI)** — `backend/tests/test_enable_rls_policy_coverage.py`
     contains `test_every_real_tenant_table_is_classified` (iterates real ORM
     metadata to catch unclassified tables before integration) and
     `test_rls_forced_tables_are_covered_by_rls_invariants` (asserts the
     invariant suite covers every auto-derived RLS-forced table).
   - **L3 agent skill** — run `python .claude/skills/rls-coverage-check/check.py`
     before finishing any change that adds or alters a per-tenant table.
   - **L4 self-healing** — `rls_forced_tenant_tables()` in
     `backend/app/db/__init__.py` derives the set of every RLS-forced tenant table from the ORM,
     and `test_rls_invariants.py` unions it into `_EFFECTIVE_TABLES` so
     a new RLS-bearing table (patient-access, user-owned, or special-cased)
     is automatically covered by the RLS-forced and fail-closed invariants
     with zero manual edits.

   Any new patient-scoped table needs an integration test that seeds two
   clinicians and asserts one cannot read or write the other's patient's rows.
