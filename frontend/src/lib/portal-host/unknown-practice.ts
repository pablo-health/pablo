// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A portal address whose slug names no practice: a 404 status, and the
 * portal's own card for the person who mistyped it.
 *
 * Calling `notFound()` from the practice layout is not enough. The page's
 * status is fixed before the app's children are rendered on the server (the
 * portal waits for its config in the browser), so a `notFound()` there
 * renders the card under a 200. A path that matches no route, though, is a
 * 404 before anything renders. So the proxy asks the backend first and, when
 * no practice holds the slug, serves {@link UNKNOWN_PRACTICE_PATH} in its
 * place with {@link UNKNOWN_PRACTICE_HEADER} set; `app/not-found.tsx` reads
 * the mark and shows the portal's card rather than the app's 404.
 *
 * A mark sent by the visitor is dropped, so only the proxy decides.
 */

import { NextRequest } from "next/server"
import { lookupPortalSlug } from "./practice-host-lookup"

export const UNKNOWN_PRACTICE_HEADER = "x-portal-unknown-practice"

/** Matches no route, so serving it answers 404 with `app/not-found.tsx`. */
export const UNKNOWN_PRACTICE_PATH = "/_portal/unknown-practice"

/**
 * `request` with any visitor-sent mark dropped, and whether the slug in
 * `portalPath` (`/portal/{slug}/...`) is held by no practice — in which case
 * the returned request carries the mark. A lookup that cannot be made is not
 * "unknown": the page is served and the shell finds out for itself.
 */
export async function checkPortalPractice(
  request: NextRequest,
  portalPath: string,
): Promise<{ request: NextRequest; unknown: boolean }> {
  const headers = new Headers(request.headers)
  headers.delete(UNKNOWN_PRACTICE_HEADER)
  const encoded = portalPath.split("/")[2] ?? ""
  const unknown = encoded !== "" && (await lookupPortalSlug(decodedSlug(encoded))) === null
  if (unknown) headers.set(UNKNOWN_PRACTICE_HEADER, "1")
  return { request: new NextRequest(request, { headers }), unknown }
}

function decodedSlug(encoded: string): string {
  try {
    return decodeURIComponent(encoded)
  } catch {
    // Not valid percent-encoding: no practice holds it, and the backend says
    // so for whatever is passed through.
    return encoded
  }
}
