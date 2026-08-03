// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Frontend error reporter.
 *
 * Captures unhandled errors and React error-boundary catches and ships
 * them to the backend's /api/internal/fe-error endpoint. No third-party
 * telemetry — Cloud Logging is the only destination, which keeps us
 * inside the same BAA boundary as the rest of the app.
 *
 * HIPAA notes:
 *
 *   - PHI is *deny-listed* before sending. The deny list lives below
 *     in `PHI_DENY_KEYS` and mirrors the scrubber applied by the
 *     receiving endpoint on the server. The two layers are
 *     redundant on purpose — the frontend must do its best, and the
 *     server will not trust the frontend.
 *   - We never capture component props, React state, error messages,
 *     or full stack traces. Only error.name, the top stack frame
 *     (normalized via `topStackFrame`), the route template, the build
 *     SHA, and the user agent are emitted.
 *   - UUIDs in the top stack frame and route template are rewritten
 *     to `{id}` so per-tenant identifiers never leak through the
 *     error path.
 *
 * Setup:
 *
 *   - Call `installGlobalErrorReporter()` once at app boot (Providers).
 *   - `ErrorBoundary.componentDidCatch` calls `reportFrontendError` for
 *     React render errors that the boundary catches.
 *   - The endpoint is optional: a deployment that does not implement
 *     it will 404 the POST. That's fine — the helper swallows network
 *     errors silently,
 *     so the frontend never breaks because of a missing endpoint.
 */

import { buildApiUrl, getAuthHeader } from "@/lib/api/client"

const FE_ERROR_ENDPOINT = "/api/internal/fe-error"

/**
 * Build-time-injected commit SHA. Vite/Next.js will inline this at
 * build via NEXT_PUBLIC_BUILD_SHA or fall back to "unknown".
 */
const BUILD_SHA: string =
  (typeof process !== "undefined" &&
    typeof process.env !== "undefined" &&
    (process.env.NEXT_PUBLIC_BUILD_SHA ||
      process.env.NEXT_PUBLIC_GIT_SHA)) ||
  "unknown"

/**
 * Keys the frontend will never include in a payload to the backend.
 * Mirrored by the scrubber on the receiving endpoint. The two must
 * stay in sync; if you add a key here, add it there too.
 */
const PHI_DENY_KEYS: ReadonlySet<string> = new Set([
  "patient_id",
  "patient_name",
  "patient_email",
  "soap_text",
  "transcript",
  "transcript_content",
  "note_content",
  "chat_message",
  "session_content",
  "audio_path",
  "session_id",
])

// RFC 4122 UUID, anywhere inside a string.
const UUID_PATTERN =
  /\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b/g

const MAX_ERROR_CLASS = 200
const MAX_STACK_FRAME = 500
const MAX_ROUTE = 200
const MAX_USER_AGENT = 300

export interface FrontendErrorEvent {
  error_class: string
  stack_top_frame: string
  route_template: string
  build_sha?: string
  user_agent?: string
}

/**
 * Strip UUIDs, drop NULs, and trim to length. Same algorithm as the
 * server-side scrubber; the two are intentionally redundant.
 */
export function scrubString(value: string, maxLen: number): string {
  const cleaned = value.replace(UUID_PATTERN, "{id}").replace(/\u0000/g, "")
  return cleaned.length > maxLen ? `${cleaned.slice(0, maxLen)}...[truncated]` : cleaned
}

/**
 * Drop any key in the deny list and any non-string value. The result
 * is what's safe to ship over the wire.
 */
export function scrubPayload(
  raw: Record<string, unknown>,
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [key, value] of Object.entries(raw)) {
    if (PHI_DENY_KEYS.has(key)) continue
    if (typeof value !== "string") continue
    out[key] = value
  }
  return out
}

/**
 * Return just the first frame from a stack string (or "unknown"
 * if we cannot parse it). We never ship the full stack — too much
 * surface for accidental PHI in URL paths, query strings, or
 * captured-variable inspection.
 */
export function topStackFrame(stack: string | undefined): string {
  if (!stack) return "unknown"
  const lines = stack.split("\n").map((line) => line.trim())
  // The first non-empty line that starts with `at ` is the frame we
  // want. V8 stacks look like:
  //   Error: boom
  //       at fn (file.js:1:1)
  //       ...
  // Firefox stacks look like:
  //   fn@file.js:1:1
  //   ...
  // Either way, the second meaningful line wins for V8; for Firefox
  // the first non-empty line is already a frame.
  const v8Frame = lines.find((line) => line.startsWith("at "))
  if (v8Frame) return v8Frame
  const firstNonEmpty = lines.find((line) => line.length > 0)
  return firstNonEmpty || "unknown"
}

/**
 * Map a window.location.pathname to a route template. We strip query
 * strings and rewrite UUIDs to `{id}` — this is the value that lands
 * in the route_template field, which is the primary grouping key on
 * the operations dashboard.
 */
export function deriveRouteTemplate(pathname: string): string {
  const noQuery = pathname.split("?")[0]
  return scrubString(noQuery || "/", MAX_ROUTE)
}

interface ReportOptions {
  /** Defaults to window.location.pathname. Pass explicitly for SSR-safe call sites. */
  routeTemplate?: string
  /** Defaults to navigator.userAgent. */
  userAgent?: string
}

/**
 * Send one frontend error event to the backend. Fire-and-forget — a
 * network failure is silently swallowed so the helper never escalates
 * itself into a new error loop.
 */
export async function reportFrontendError(
  error: Error,
  options: ReportOptions = {},
): Promise<void> {
  if (typeof window === "undefined") return

  const payload: FrontendErrorEvent = {
    error_class: scrubString(error.name || "Error", MAX_ERROR_CLASS),
    stack_top_frame: scrubString(topStackFrame(error.stack), MAX_STACK_FRAME),
    route_template: scrubString(
      options.routeTemplate ?? window.location.pathname,
      MAX_ROUTE,
    ),
    build_sha: BUILD_SHA,
    user_agent: scrubString(
      options.userAgent ?? navigator.userAgent ?? "",
      MAX_USER_AGENT,
    ),
  }

  try {
    const url = buildApiUrl(FE_ERROR_ENDPOINT)
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(await getAuthHeader()),
    }
    // No-auth installs (logged-out users hitting a 500 on the login
    // page) would still post; the backend will return 401 and we
    // swallow it. Same shape as a 404 against pure-OSS.
    await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify(scrubPayload({ ...payload })),
      keepalive: true,
    })
  } catch {
    // Reporting must never throw — the alternative is an infinite
    // loop of error -> reporter -> error.
  }
}

let installed = false

/**
 * Wire window.onerror and unhandledrejection to reportFrontendError.
 * Idempotent (safe to call from React StrictMode double-mounts) and a
 * no-op on the server.
 */
export function installGlobalErrorReporter(): void {
  if (typeof window === "undefined" || installed) return
  installed = true

  window.addEventListener("error", (event) => {
    const error =
      event.error instanceof Error
        ? event.error
        : new Error(typeof event.message === "string" ? event.message : "window-error")
    void reportFrontendError(error)
  })

  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason
    const error =
      reason instanceof Error
        ? reason
        : new Error(
            typeof reason === "string" ? reason : "unhandled-rejection",
          )
    void reportFrontendError(error)
  })
}

// Test seam — let unit tests reset the installed flag between runs.
export function _resetForTests(): void {
  installed = false
}
