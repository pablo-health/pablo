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
