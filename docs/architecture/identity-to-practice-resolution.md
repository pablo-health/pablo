# An identity resolves to a practice by mapping, not by counting

**Status:** decided, 2026-09-08

## The decision

When an email is granted access, the grant and the identity → practice
mapping are written **as one operation**. Resolution on the login path is
then a single lookup with no conditionals about how many practices exist.

The alternative — "if exactly one practice is registered, that is the
answer" — was rejected.

## Why this needed deciding at all

A deployment used to be able to skip practice resolution entirely. With
that skipped, an operator added an email to `allowed_emails` and that was
the whole story: the request got a context with no practice attached, and
the checks that depend on knowing the practice did not run.

Once every deployment runs a real practice schema, resolution is on the
login path for every user, and an identity that resolves to nothing cannot
sign in. So the question stops being rhetorical: what *does* an email
resolve to?

## Why mapping rather than counting

Counting is cheaper — no backfill, no second write. It was rejected
anyway, for two reasons.

The first is that "if there is only one, use it" is a special case, and it
would sit on the authentication path, which is the worst place to keep
one. Every later reader has to hold two possibilities in mind, and the
rare branch is the one that gets least exercise and least review.

The second is that the special case is load-bearing in exactly the
situation where it is least true. A deployment grows a second practice on
the day somebody adds one — and on that day, every identity that had been
resolving by "there is only one" silently starts resolving by something
else, or stops resolving at all. The failure would land on the login path
of users who changed nothing.

One lookup, always, is duller and does not have that day in it.

## What it costs

Two things, both accepted.

**A backfill.** Deployments that granted access before this have
`allowed_emails` rows with no matching mapping. Those identities resolve
to nothing until they are mapped. The migration that moves an existing
single-practice deployment onto a provisioned schema carries the backfill
with it, and its pre-flight is what proves no user is left unresolvable.

**Two registries that must agree.** `allowed_emails` answers "may this
person in", `email_tenant_mappings` answers "into which practice". They
are separate tables and could drift. The mitigation is that nothing
writes one without the other: `AllowlistRepository.add` takes a
`practice_id` with no default and writes both rows in the same session,
and `remove` retires both. That they remain two tables rather than one is
a reasonable thing to revisit; it is not what makes them safe today.

## Consequences worth knowing

`_await_provisioning_ready` now runs on the sign-in path for every user
rather than only for deployments with more than one practice. Its timeout
is therefore a sign-in concern: a tenant still provisioning returns 503
with `Retry-After` rather than an error the user can do anything about.

An admin who is not attached to a practice cannot invite anyone — the
route refuses rather than writing a grant that would resolve to nothing.
That is a real behaviour change and it is deliberate: an invitation is
into somebody's practice, and an invitation into no practice is an
account that signs in successfully and lands nowhere.

## Memberships are recorded beside the mapping (2026-11-17)

An address is a poor key for an account: it can change, and an old address
can later be granted somewhere else. So each account's practice is now also
recorded against the account itself, in `platform.practice_members` (one row
per account). At first the row was recorded and compared, and decided
nothing: the mapping above still routed every request. Since 2026-11-19 the
membership decides (next section); the points below still hold.

- **Who writes it.** A request whose address the sign-in token verified
  (`get_tenant_context`, the schema middleware, and the guard that keeps a
  first sign-in off the template) writes the account's row the first time it
  resolves through the mapping (`joined_via = 'email_claim'`). On the request
  that creates the account, the account's own row is not committed yet, so the
  membership is written when that request commits; a request that fails
  writes neither.
  Callers that pass the address stored on the account never write: that
  address is set once and not refreshed. The migrate step gives every
  already-mapped account its row (`'backfill'`) and prints how many mapped
  accounts are still without one.
- **The grant is checked as the row is written.** The claim is one statement
  that inserts only while the address is still mapped to that practice and the
  practice is active, so a revoke that lands mid-request leaves nothing behind.
- **Disagreement is logged.** When an account's row names a different
  practice than its address maps to, `practice_membership_disagrees` is
  logged at INFO with the account and both practice ids, never the address.
  That log was the evidence for letting the membership route.
- **A membership does not outlive its practice.** Whatever deactivates a
  practice calls `app.db.practice_members.remove_for_practice` in the same
  transaction.

## The membership decides (2026-11-19)

The comparison above ran on a deployed build and found no account whose
membership and mapping disagreed, so the membership now routes. A signed-in
request resolves the **account** first:

- **An account with a membership goes to that practice.** If the practice is
  no longer active, the account resolves to nothing; the mapping is not read
  as a fallback, so an address cannot route an account around its
  membership. When the address maps somewhere else, the membership still
  wins and `practice_membership_disagrees` is logged with ids only.
- **The mapping decides only the first sign-in.** An account with no
  membership resolves through its address's mapping, and that request
  writes its membership as before. From the next request on, the account
  decides. There is still one lookup and no counting: membership, then
  mapping, then nothing.
- **One answer per request.** The schema middleware and the auth
  dependencies after it share one resolution, so a request reads the
  membership at most once and logs a disagreement at most once. The answer
  is also cached in Redis per account for a short time
  (`practice-resolution:v1:user:<user_id>`), and a membership written, moved
  or removed, or an account deleted, drops that entry when it commits.

What this means for the allowlist:

- **Revoking a grant removes the membership it led to.**
  `AllowlistRepository.remove` deletes the membership claimed with the
  revoked address (`claimed_email`) through the grant (`email_claim` or
  `backfill`), wherever it is. It matches the address the membership was
  claimed with, not the one stored on the account, which is written once
  and never refreshed. It works when the grant names no practice, and after
  a re-grant has moved the mapping elsewhere. A membership reached by
  invitation is left alone. The account's next request resolves to nothing.
- **Re-granting does not move a member.** Granting an address to another
  practice moves the mapping, but an account that has already claimed a
  membership stays where it is, and the disagreement is logged. Until a
  tool for moving a member exists, move an account by revoking its grant
  and then granting the new practice: its next sign-in claims the new
  practice through the mapping.
