// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Fail a spec when the backend returns 5xx to any request the browser made —
 * not only the ones a spec happens to assert on.
 *
 * Specs assert on what a user can see. A page-load fan-out can lose one panel
 * to a 500 and still render, still satisfy every assertion, and still pass —
 * so a route can fault on every run without a single test noticing. Watching
 * the network rather than the rendered result closes that.
 *
 * 5xx only, deliberately. These suites produce plenty of legitimate 401s and
 * 403s — unauthenticated probes and permission checks the specs exercise on
 * purpose — so guarding any non-2xx would flake constantly. A 5xx is never
 * expected, which is what makes it safe to let one fail a run.
 *
 * KNOWN_OPEN is empty and should stay that way. The local stack starts from a
 * fresh database on every run, so there is no inherited backlog to excuse. If
 * an entry ever becomes necessary it must cite the issue tracking the fix, and
 * the list may only shrink — a waiver left behind after its bug is gone is
 * worse than none, because a regression then passes unnoticed.
 */

import type { Page, Response } from "@playwright/test"

/** One observed server error, normalized for reporting and matching. */
export interface ServerError {
  status: number
  method: string
  /** Path with id-like segments replaced, so reports group by route. */
  route: string
}

const KNOWN_OPEN: { route: RegExp; status?: number; issue: string; why: string }[] = []

const UUID = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

/** `/api/patients/<uuid>/charges` -> `/api/patients/{id}/charges`. */
export function normalizeRoute(rawUrl: string): string {
  let path: string
  try {
    path = new URL(rawUrl).pathname
  } catch {
    return rawUrl
  }
  return path
    .split("/")
    .map((seg) => (UUID.test(seg) || /^\d+$/.test(seg) ? "{id}" : seg))
    .join("/")
}

export function isKnownOpen(error: ServerError): boolean {
  return KNOWN_OPEN.some(
    (known) =>
      known.route.test(error.route) &&
      (known.status === undefined || known.status === error.status),
  )
}

export interface ServerErrorGuard {
  /** Every 5xx observed, including any that are excused. */
  all: ServerError[]
  /** Throws with a readable summary if an unexcused 5xx was seen. */
  assertNone: () => void
  dispose: () => void
}

export function attachServerErrorGuard(page: Page): ServerErrorGuard {
  const all: ServerError[] = []

  const onResponse = (response: Response) => {
    const status = response.status()
    if (status < 500) return
    all.push({
      status,
      method: response.request().method(),
      route: normalizeRoute(response.url()),
    })
  }

  page.on("response", onResponse)

  return {
    all,
    assertNone: () => {
      const unexpected = all.filter((e) => !isKnownOpen(e))
      if (unexpected.length === 0) return
      const lines = unexpected.map((e) => `  ${e.status} ${e.method} ${e.route}`)
      throw new Error(
        `The backend returned ${unexpected.length} server error(s) during this spec:\n` +
          `${lines.join("\n")}\n\n` +
          `A 5xx is never expected. Fix it, or — only if it is a known-open bug — ` +
          `add it to KNOWN_OPEN in e2e/fixtures/serverErrorGuard.ts with the issue ` +
          `tracking the fix. Never add one to quiet a flaky spec; a 5xx is not flake.`,
      )
    },
    dispose: () => {
      page.off("response", onResponse)
    },
  }
}
