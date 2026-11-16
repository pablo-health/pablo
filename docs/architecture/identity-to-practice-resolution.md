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

## Amended 2026-11-16: the mapping decides once, then the account does

The mapping above is keyed by address, while the identity it resolves is
keyed by the provider's subject id. Two keys deciding one thing can
disagree. An account whose address changed after its first sign-in was
routed by the old address, and a clinician who joined a practice under a
different address from the one they were mapped at had no way in.

So a signed-in request now resolves the **account** first:
`platform.practice_members` holds one row per account (`user_id` is the
primary key) naming its practice. The email mapping is the first claim:
an account with no membership resolves through its address's mapping, and
that request writes the membership (`joined_via = 'email_claim'`). It
writes it only when the account id is actually known, never under the
provider's subject id. From then on the membership decides. When the
address maps to a different practice, the membership wins and the
disagreement is logged with ids only. The migrate step backfills a
membership for every account that was already mapped.

Nothing above changes. `allowed_emails` and `email_tenant_mappings` stay,
`AllowlistRepository.add` still writes both, and it is still how an
address is admitted and how its first sign-in finds a practice. There is
still one lookup and no counting: membership, then mapping, then nothing.

Two things follow for the allowlist. Revoking a grant
(`AllowlistRepository.remove`) also deletes the membership of the account
with that address when the membership is in the practice the grant or
mapping named, or when the allowlist produced it (`email_claim` or
`backfill`) in any practice. Without that, the account's membership would
keep resolving it to the practice after its grant and mapping were gone,
including after a re-grant had moved the grant elsewhere. A membership
reached by invitation in a practice the grant never named is left alone.

Re-granting an address to another practice (`add` with a new
`practice_id`) moves the mapping, but it no longer moves an account that
has already claimed a membership. The membership wins, and the
disagreement is logged. Moving a member is meant to be a deliberate act,
and no tool performs it yet. Until one does, an admin can move a claimed
account by revoking the grant and granting the new practice, and the
account's next sign-in claims the new practice through the mapping.
Invitations (PABLO-oqdx) are the natural home for that tool.
