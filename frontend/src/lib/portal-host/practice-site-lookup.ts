// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Asking the backend about a practice's website hosts, and fetching their files.
 *
 * `GET {API_URL}/api/sites/hosts/{host}` answers `{primary_host}` for a
 * working website host of a practice that has published, and 404 for every
 * other; answers are kept for a minute like a portal host's
 * (`./practice-host-lookup`).
 *
 * `GET {API_URL}/api/sites/hosts/{host}/file?path=` answers a file of the
 * live version. It is fetched afresh for every request — the backend keeps
 * files in memory — and only its body, status, type and ETag are passed on;
 * the visitor's answer carries this server's own headers (`./practice-site`).
 */

import { createHostLookup, serverApiUrl } from "./practice-host-lookup"
import { SITE_SECURITY_HEADERS, type SiteHost, siteCacheControl } from "./practice-site"

const FILE_TIMEOUT_MS = 10_000

function parseSiteHost(body: unknown): SiteHost | "unavailable" {
  if (typeof body !== "object" || body === null) return "unavailable"
  const { primary_host: primaryHost } = body as Record<string, unknown>
  if (primaryHost !== null && typeof primaryHost !== "string") return "unavailable"
  return { primaryHost }
}

export const lookupSiteHost = createHostLookup<SiteHost>(
  { apiUrl: serverApiUrl },
  { path: (hostname) => `/api/sites/hosts/${encodeURIComponent(hostname)}`, parse: parseSiteHost },
)

export interface SiteFileRequest {
  /** The website host, normalized. */
  hostname: string
  /** The address the visitor asked for, which a redirect is resolved against. */
  address: string
  /** The path as the visitor sent it, still percent-encoded. */
  pathname: string
  method: string
  ifNoneMatch: string | null
}

export interface SiteFileOptions {
  apiUrl?: () => string
  fetch?: typeof fetch
}

function plain(status: number, body: string, extra: Record<string, string> = {}): Response {
  return new Response(body, {
    status,
    headers: { ...SITE_SECURITY_HEADERS, "Content-Type": "text/plain; charset=utf-8", ...extra },
  })
}

/** The visitor's answer for one file of the website `request.hostname` serves. */
export async function fetchSiteFile(request: SiteFileRequest, options: SiteFileOptions = {}): Promise<Response> {
  const fetchFn = options.fetch ?? fetch
  const base = (options.apiUrl ?? serverApiUrl)().replace(/\/+$/, "")
  const url =
    `${base}/api/sites/hosts/${encodeURIComponent(request.hostname)}/file` +
    `?path=${encodeURIComponent(request.pathname)}`
  let upstream: Response
  try {
    upstream = await fetchFn(url, {
      headers: request.ifNoneMatch ? { "If-None-Match": request.ifNoneMatch } : {},
      cache: "no-store",
      redirect: "manual",
      signal: AbortSignal.timeout(FILE_TIMEOUT_MS),
    })
  } catch {
    return plain(503, "Service Unavailable", { "Retry-After": "5" })
  }

  const headers = new Headers(SITE_SECURITY_HEADERS)
  const etag = upstream.headers.get("etag")
  if (etag) headers.set("ETag", etag)
  switch (upstream.status) {
    case 301: {
      // Relative to the address the visitor asked for (`about` -> `about/`);
      // a response from the proxy must carry it whole.
      const location = upstream.headers.get("location")
      if (!location) return plain(503, "Service Unavailable", { "Retry-After": "5" })
      headers.set("Location", new URL(location, request.address).toString())
      return new Response(null, { status: 301, headers })
    }
    case 304:
      headers.set("Cache-Control", siteCacheControl(304, null))
      return new Response(null, { status: 304, headers })
    case 200:
    case 404: {
      const contentType = upstream.headers.get("content-type")
      if (contentType) headers.set("Content-Type", contentType)
      headers.set("Cache-Control", siteCacheControl(upstream.status, contentType))
      const body = request.method === "HEAD" ? null : upstream.body
      return new Response(body, { status: upstream.status, headers })
    }
    default:
      return plain(503, "Service Unavailable", { "Retry-After": "5" })
  }
}

