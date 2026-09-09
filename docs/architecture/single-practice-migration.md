# Moving an existing deployment onto its own practice schema

A deployment created before the provisioning template and the live practice were
separated keeps its charts in `practice` — the schema that is *also* the
template every new practice is cloned from, and the one `enable_rls_on_schema`
deliberately skips. So the schema holding real records runs with no row policies
at all.

Boot now provisions `practice_default` and registers that. An existing
deployment is not touched automatically: moving live charts is a migration with
a pre-flight, not a line in a startup path.

## Running it

```bash
python backend/bin/migrate_default_practice.py --check   # report only, changes nothing
python backend/bin/migrate_default_practice.py           # pre-flight, then migrate
```

The first is safe to run any time, including against production, and is worth
running well before you intend to migrate — it tells you whether the data needs
fixing first, which is the part that can take time.

Boot **refuses to start** against an unmigrated deployment and prints these
commands. That is deliberate: the previous behaviour warned and carried on,
which meant serving a chart schema with no isolation while everything looked
healthy.

## What the pre-flight is actually checking

The migration turns `FORCE ROW LEVEL SECURITY` on over records that have been
living without it. Under FORCE RLS, a row that satisfies no policy **does not
raise — it becomes invisible**. A chart that is silently not there reads as "the
patient was deleted" or "search is broken", and nobody connects it to a
migration that reported success weeks earlier.

So the pre-flight counts, per table, the rows that would be readable by *nobody*
once policies apply:

| Policy shape | A row is unreachable when |
|---|---|
| `user_id` ownership | `user_id IS NULL` |
| `audit_logs` actor split | `user_id IS NULL` |
| `compliance_documents` | `uploaded_by_user_id IS NULL` |
| `patients` | no live `patient_clinicians` grant for its `id` |
| any table with `patient_id` | no live grant for that `patient_id` |
| `patient_documents` | chart categories: no live grant; restricted categories: `user_id IS NULL` |
| `chat_messages` | parent conversation missing, or its patient has no live grant |

"Live" includes `expires_at` — an expired grant hides the row just as a missing
one does, and a liveness-blind check would call the migration safe while charts
vanish.

Migration `777b846ab944` backfilled `patient_clinicians` from `patients.user_id`
when the access table landed, so grants *should* exist for every row. "Should"
is the wrong confidence for an operation whose failure mode is disappearing
records, which is why this is a refusal rather than a warning.

**`patients` carries no `user_id` column.** Ownership lives entirely in
`patient_clinicians`. That is exactly why a missing grant is unrecoverable
rather than merely inconvenient: there is no column left on the row that says
whose chart it was.

## Identities, and why nobody gets locked out

Resolution reads `platform.email_tenant_mappings` and nothing else. Before
`AllowlistRepository.add` started writing that mapping alongside the grant, an
operator added an email to `allowed_emails` and that was the whole story — the
request got a context with no practice attached. Those identities **resolve to
nothing**.

While a deployment could skip resolution entirely, that was survivable. Once
every deployment runs a real practice schema, resolution is on the login path
for every user, and an identity that resolves to nothing **cannot sign in**.

So this migration carries the backfill, as
[`identity-to-practice-resolution.md`](identity-to-practice-resolution.md)
specifies. It maps every identity the platform knows about — everyone in
`platform.users`, plus everyone in `allowed_emails` who has not signed up yet —
onto the deployment's practice, writing the same `tenant_id`/`practice_id` pair a
grant writes, so a backfilled row is indistinguishable from a granted one.

The backfill runs **first**, before anything is renamed, so a failure there
leaves the deployment exactly as it was. Afterwards the migration re-checks and
**refuses** if any identity still resolves to nothing: a leftover means something
wrote a mapping-less identity this code does not model, and whether people can
sign in is not a thing to find out about afterwards.

`--check` reports unresolvable identities without failing, because unlike the row
counts this is not something to fix by hand — the migration fixes it.

## What it does, in order

0. backfill `email_tenant_mappings` for every unresolvable identity
1. `ALTER SCHEMA practice RENAME TO practice_default`
2. re-point `platform.practices` at the new name
3. rebuild `practice` from the tenant template (it is a template again)
4. `enable_rls_on_schema(practice_default)`

Steps 1 and 2 share one transaction: a deployment whose schema is renamed but
whose registry still names the old one is unreachable in a way that is much
harder to reason about than either end state.

RLS goes on **last**, after the registry says the schema is a practice. Enabling
it earlier would arm the guards against a schema the rest of the system still
believes is the template.

Running it twice is safe — the second run sees the registry already pointing at
`practice_default` and does nothing.

## Rollback

**There is no automated reverse, and you should not write one.** The forward
migration is a schema rename plus a registry update, so the mechanical reverse
is trivial — and that is the trap. Once the application has been up on the
migrated schema, rows have been written under the policies. Renaming back does
not un-write them, and the old shape has no policies to describe who owns what,
so the reverse silently widens access to everything written since.

If you must go back:

1. **Stop the application.** Not drain — stop. A single write during the reverse
   lands in a schema that is about to be renamed out from under it.
2. **Restore from the backup taken before the migration.** This is the supported
   path. Point-in-time recovery to a timestamp before step 1 of the forward
   migration gives you the old shape exactly, including the registry row.
3. Deploy a backend from before the boot refusal landed, or the restored
   deployment will refuse to start — correctly, because it is once again running
   without row policies.

If restoring is genuinely impossible and you accept the access-widening, the
reverse is: `ALTER SCHEMA practice_default RENAME TO practice_rollback`, drop the
rebuilt `practice` template, rename `practice_rollback` back to `practice`,
re-point `platform.practices`, and drop every policy on it. Write down that the
deployment is running without isolation, and treat re-migrating as urgent.

**Take a backup before running the forward migration.** The pre-flight makes
data loss unlikely, not impossible.
