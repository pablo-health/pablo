// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Public (unauthenticated) routes the engine itself serves.
 *
 * The patient portal is one: the person on `/portal/*` holds a portal
 * session token, never a clinician sign-in, so sending them to `/login`
 * offers them a door they have no key to. It is listed here rather than
 * left to `EXTRA_PUBLIC_PATHS` because every deployment that serves the
 * portal needs it, so no deployment should have to remember it.
 *
 * Prefix-matched, exactly like the deployment-contributed entries below.
 */
export function builtInPublicPaths(): string[] {
  return ["/portal"]
}

/**
 * Deployment-contributed public (unauthenticated) routes.
 *
 * A deployment can serve routes this repo has no knowledge of, and some of
 * them legitimately need to be reachable without a session — a page whose
 * one-time token in the URL *is* the credential, for example. Rather than
 * teach the auth middleware about every such route, a deployment names them
 * in `EXTRA_PUBLIC_PATHS` and the provider middlewares union them into their
 * own allowlists.
 *
 * Comma-separated, prefix-matched exactly like the built-in entries. Entries
 * must be absolute paths; anything not starting with "/" is dropped rather
 * than silently widening the match. Unset (the default) returns an empty
 * list, so a deployment that sets nothing behaves exactly as before.
 */
export function extraPublicPaths(): string[] {
  return (process.env.EXTRA_PUBLIC_PATHS ?? "")
    .split(",")
    .map((p) => p.trim())
    .filter((p) => p.startsWith("/"))
}
