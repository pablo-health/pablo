// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * API Client
 *
 * Centralized HTTP client for backend API communication.
 * Handles authentication, error handling, and type-safe responses.
 */

import { getClientAuthProvider } from "@/lib/auth/provider"
import { apiErrorInterceptors } from "./client.extensions"

/**
 * Observe a failed response before its body is consumed and return a partial
 * to assign onto the thrown `ApiError` (or null to do nothing). A downstream
 * build registers these via `client.extensions.ts` — see that file. Guard on
 * `response.status` first so successful responses pay only a status check.
 */
export type ApiErrorInterceptor = (
  response: Response,
) => Promise<Record<string, unknown> | null>

/**
 * Observe every response (successful or not) after the fetch resolves and
 * before its body is parsed. Receives a `response.clone()`, so an interceptor
 * may read the body without stealing it from the request path. Return value is
 * ignored — this is an observation hook (maintenance-mode banners, telemetry),
 * not a place to mutate the response. Anything thrown is swallowed: observing a
 * response must never break the request it rode in on.
 */
export type ApiResponseInterceptor = (
  response: Response,
) => void | Promise<void>

const apiResponseInterceptors: ApiResponseInterceptor[] = []

/**
 * Register a response observer without forking the client. A deployment that
 * needs to react to response shapes — surface a maintenance banner, emit
 * telemetry — registers here instead of copying `apiClient`, so it can't drift
 * from upstream fixes. Returns a disposer that removes the interceptor again
 * (handy for tests and for teardown of short-lived observers).
 */
export function registerResponseInterceptor(fn: ApiResponseInterceptor): () => void {
  apiResponseInterceptors.push(fn)
  return () => {
    const i = apiResponseInterceptors.indexOf(fn)
    if (i !== -1) apiResponseInterceptors.splice(i, 1)
  }
}

let terminalAuthLogoutInFlight = false

/**
 * Sign the user out and bounce to /login for an unrecoverable auth failure —
 * an idle timeout, or a session whose token is expired/revoked/disabled and
 * can't be refreshed. Without this the request just throws an ApiError and the
 * user is stranded on a logged-in-looking page that errors on every action.
 *
 * Re-entry guarded so a burst of parallel 401s (a single dashboard load fires
 * several requests at once) yields ONE sign-out + redirect, not many. ``reason``
 * is surfaced on the login screen.
 *
 * Exported so every surface that detects a dead session — the chat SSE
 * consumer, blob downloads, and the IdleTimeout controller's server peek —
 * drives the identical sign-out flow instead of growing its own.
 */
export function handleTerminalAuthLogout(reason: "idle_timeout" | "session_expired") {
  if (terminalAuthLogoutInFlight) return
  terminalAuthLogoutInFlight = true
  void (async () => {
    try {
      // Wipe the persisted SDK session, not just the in-memory one. For an idle
      // timeout the session is tombstoned server-side by its `auth_time`, which
      // survives a Firebase token refresh; for an expired/revoked session the
      // stored refresh token is itself dead. Either way, if the SDK re-hydrates
      // the old session from storage (bfcache / iOS Safari) the user loops on
      // the same 401 forever. Forcing a fresh sign-in mints a new one.
      await getClientAuthProvider().signOut({ wipePersisted: true })
    } catch {
      // Provider not initialized — still redirect.
    }
    window.location.assign(`/login?reason=${reason}${returnToParam()}`)
  })()
}

/**
 * The `&returnTo=` fragment that sends the user back where they were once they
 * sign in again, or "" when there is nowhere sensible to return to.
 *
 * An idle timeout is not a navigation the user asked for — it fires on a timer,
 * mid-task, from whatever page they were on. Without this the sign-in that
 * follows lands them on the dashboard, so stepping away from a half-written
 * note costs them their place with no way back but the browser's Back button.
 *
 * Only the path, query, and hash are carried, never an absolute URL: the value
 * is read back off the query string on the login screen, and a full URL there
 * would be an open redirect. `/login` itself is excluded so a boot that somehow
 * fires on the login screen can't loop back into it.
 */
export function returnToParam(): string {
  if (typeof window === "undefined") return ""
  // Each part defaulted rather than read straight off `location`: this runs
  // under test doubles that stand in a partial location object, where the raw
  // concatenation would produce NaN instead of a string.
  const loc = window.location
  const here = `${loc?.pathname ?? ""}${loc?.search ?? ""}${loc?.hash ?? ""}`
  if (!here.startsWith("/") || here.startsWith("//")) return ""
  if (here === "/" || here.startsWith("/login")) return ""
  return `&returnTo=${encodeURIComponent(here)}`
}

/**
 * Global runtime configuration
 * Set by ConfigProvider on app initialization (client-side only)
 */
let runtimeApiUrl = 'http://localhost:8000'

/**
 * Set the API URL at runtime
 * Called by ConfigProvider after fetching config from /api/config
 */
export function setApiUrl(url: string) {
  runtimeApiUrl = url
}

/**
 * Get the API URL, handling both server and client contexts
 * Server-side: uses API_URL environment variable
 * Client-side: uses runtimeApiUrl set by ConfigProvider
 */
function getApiUrl(): string {
  if (typeof window === 'undefined') {
    return process.env.API_URL || 'http://localhost:8000'
  }
  return runtimeApiUrl
}

export interface ApiErrorResponse {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
}

/**
 * Pull the `{ error: { code, message, details } }` envelope out of an error
 * body, at either of the two depths the API actually emits it.
 *
 * NESTED is the one that was being missed. A route that refuses a request by
 * raising `HTTPException(detail={"error": {...}})` does not reach us in that
 * shape: `register_exception_handlers` deliberately passes `HTTPException`
 * through to FastAPI's own renderer, and that renderer wraps whatever detail
 * it was handed under a `detail` key. So the envelope arrives one level down:
 *
 *     {"detail": {"error": {"code": "IDLE_TIMEOUT", …}}}
 *
 * Reading only the top level meant every one of those raise sites resolved to
 * `UNKNOWN_ERROR`, which quietly disabled all three things downstream of the
 * code: the idle-timeout logout, the terminal-auth logout, and the
 * force-refresh retry for an expired token. Nothing errored — the codes simply
 * never matched, so a session that should have been recovered or ended cleanly
 * instead stayed half-alive and every later request failed on its own.
 *
 * FLAT is what a body written straight to a `JSONResponse` looks like, which
 * is what the `APIError` handler produces. Both shapes are real, so read both
 * rather than betting on one.
 *
 * A `detail` that is not an envelope (some sites pass a bare string) falls
 * through to the top level and then fails the `error` lookup — the same "not
 * one of ours" answer as a body with no envelope at all.
 */
function findErrorEnvelope(data: unknown): ApiErrorResponse | null {
  if (typeof data !== "object" || data === null) return null
  const top = data as Record<string, unknown>
  const detail = top.detail
  if (typeof detail === "object" && detail !== null && "error" in detail) {
    return detail as unknown as ApiErrorResponse
  }
  return top as unknown as ApiErrorResponse
}

export class ApiError extends Error {
  /**
   * Set by a registered error interceptor to mean: an explanation has
   * already been surfaced to the user (a dialog, a flash) for this failure.
   * Callers must not render their own generic error copy, and a caller that
   * is itself a modal must close so that explanation is not occluded.
   */
  public handledExternally?: boolean

  constructor(
    public code: string,
    message: string,
    public details?: Record<string, unknown>,
    public status?: number
  ) {
    super(message)
    this.name = "ApiError"
  }
}

interface FetchOptions extends RequestInit {
  token?: string
}

/**
 * Backend auth error codes that mean "the id token we sent was stale" —
 * as opposed to a real authorization failure (MFA_REQUIRED) or a
 * deliberately-terminated session (IDLE_TIMEOUT). These are recoverable
 * by minting a fresh token and retrying once; the others are not.
 *
 * Firebase's proactive token refresh lags in backgrounded / throttled
 * tabs, so a cached id token can outlive its 1h expiry and reach the
 * backend dead. ``getIdToken(true)`` force-refreshes past that.
 */
export const TOKEN_REFRESH_RETRY_CODES = new Set(["TOKEN_EXPIRED", "INVALID_TOKEN"])

/**
 * Backend auth 401 codes that mean the session is unrecoverable — the token is
 * expired (and a refresh didn't help), malformed/invalid, revoked, or the
 * account is disabled. These boot the user to /login rather than letting the
 * request throw and strand them. Deliberately EXCLUDES ``MFA_REQUIRED`` (a
 * step-up flow handled inline) and ``IDLE_TIMEOUT`` (handled separately, with
 * its own reason). ``TOKEN_REVOKED`` / ``USER_DISABLED`` are terminal on
 * arrival — no refresh is attempted for them.
 */
export const TERMINAL_AUTH_CODES = new Set([
  "TOKEN_EXPIRED",
  "INVALID_TOKEN",
  "TOKEN_REVOKED",
  "USER_DISABLED",
])

/**
 * Resolve the bearer token for an authenticated request.
 *
 * Client-side: prefers the supplied token, else asks the active auth
 * provider for the current user's id token. Server-side: returns the
 * supplied token or null. Shared by ``apiClient`` and the SSE consumer so
 * SSE calls land with the same auth posture as regular API calls without
 * going through the JSON fetch wrapper.
 *
 * ``forceRefresh`` bypasses the provider's cached token — used by the
 * retry path after a token-level 401. The provider owns any token-restore
 * races (e.g. Firebase's IndexedDB restoration window).
 */
export async function getAuthHeader(
  token?: string,
  forceRefresh = false,
): Promise<Record<string, string>> {
  let authToken = token
  if (!authToken && typeof window !== "undefined") {
    try {
      authToken = (await getClientAuthProvider().getIdToken(forceRefresh)) ?? undefined
    } catch {
      // Provider not initialized or no current user — proceed without token.
    }
  }
  return authToken ? { Authorization: `Bearer ${authToken}` } : {}
}

export function buildApiUrl(endpoint: string): string {
  return `${getApiUrl()}${endpoint}`
}

/**
 * Read the backend error ``code`` from a non-OK JSON response *without*
 * consuming its body — clones first so the caller can still read the
 * full error payload afterward. Returns null for non-JSON or unparseable
 * bodies.
 */
async function peekErrorCode(response: Response): Promise<string | null> {
  const contentType = response.headers.get("content-type")
  if (!contentType?.includes("application/json")) return null
  try {
    const data = findErrorEnvelope((await response.clone().json()) as unknown)
    return data?.error?.code ?? null
  } catch {
    return null
  }
}

/**
 * Make an authenticated API request
 *
 * Client-side: gets the token from the active auth provider
 * Server-side: token must be passed explicitly via the `token` option
 */
export async function apiClient<T>(
  endpoint: string,
  options: FetchOptions = {}
): Promise<T> {
  const { token, ...fetchOptions } = options

  const url = buildApiUrl(endpoint)

  const doFetch = async (forceRefresh: boolean): Promise<Response> => {
    const headers: Record<string, string> = {
      // Don't set Content-Type for FormData — browser must set it with multipart boundary
      ...(fetchOptions.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...(fetchOptions.headers as Record<string, string>),
      ...(await getAuthHeader(token, forceRefresh)),
    }
    return fetch(url, { ...fetchOptions, headers })
  }

  try {
    let response = await doFetch(false)

    // A token-level 401 (stale/expired id token) is recoverable: force a
    // token refresh and retry the request once. Scoped to client-managed
    // tokens (no caller-supplied `token`) and to the token-error codes —
    // IDLE_TIMEOUT must stay terminal (a refresh would defeat the idle
    // control) and is handled below.
    if (response.status === 401 && !token && typeof window !== "undefined") {
      const retryCode = (await peekErrorCode(response)) ?? ""
      if (TOKEN_REFRESH_RETRY_CODES.has(retryCode)) {
        response = await doFetch(true)
      }
    }

    // Response observers run on every response, before the body is consumed
    // below. Each gets a clone so it can read the body without stealing it from
    // the request path, and a throwing observer is swallowed — observation must
    // never break the request. Empty by default.
    for (const interceptor of apiResponseInterceptors) {
      try {
        await interceptor(response.clone())
      } catch {
        // An observer must not break the request path.
      }
    }

    // Error-path interceptors run before the body is consumed below. A
    // downstream build uses these to detect a condition (e.g. a maintenance
    // 503) and ride extra fields onto the thrown ApiError, so a server-side
    // caller can react without the client-only stores. Empty by default.
    let interceptorData: Record<string, unknown> | null = null
    for (const interceptor of apiErrorInterceptors) {
      const data = await interceptor(response)
      if (data) interceptorData = { ...(interceptorData ?? {}), ...data }
    }

    if (response.ok) {
      const contentType = response.headers.get("content-type")
      if (contentType?.includes("application/json")) {
        // 204s (and some proxies' empty replies) still carry an
        // application/json content-type; .json() on an empty body throws
        // and turns a successful mutation into a phantom error.
        const text = await response.text()
        return (text ? JSON.parse(text) : undefined) as T
      }
      return (await response.text()) as unknown as T
    }

    let errorData: ApiErrorResponse | null = null
    const contentType = response.headers.get("content-type")

    if (contentType?.includes("application/json")) {
      try {
        errorData = findErrorEnvelope((await response.json()) as unknown)
      } catch {
        // Failed to parse error response
      }
    }

    const errorCode = errorData?.error?.code || "UNKNOWN_ERROR"
    const errorMessage =
      errorData?.error?.message ||
      `API request failed with status ${response.status}`
    const errorDetails = errorData?.error?.details

    // An unrecoverable 401 boots the user to /login instead of stranding them
    // on a logged-in-looking page that throws on every action. IDLE_TIMEOUT
    // mirrors the client-side IdleTimeout component. A token-level failure that
    // survived the force-refresh retry above (expired/invalid) or is terminal
    // on arrival (revoked/disabled) sends the user to a fresh sign-in — scoped
    // to client-managed tokens (no caller-supplied `token`), since a caller
    // that owns its token owns its own auth handling. Both paths are re-entry
    // guarded so a parallel burst of 401s fires one redirect. MFA_REQUIRED is
    // intentionally excluded (it drives the step-up flow, not a logout).
    if (response.status === 401 && typeof window !== "undefined") {
      if (errorCode === "IDLE_TIMEOUT") {
        handleTerminalAuthLogout("idle_timeout")
      } else if (!token && TERMINAL_AUTH_CODES.has(errorCode)) {
        handleTerminalAuthLogout("session_expired")
      }
    }

    const apiError = new ApiError(errorCode, errorMessage, errorDetails, response.status)
    if (interceptorData) Object.assign(apiError, interceptorData)
    throw apiError
  } catch (error) {
    if (error instanceof ApiError) {
      throw error
    }

    throw new ApiError(
      "NETWORK_ERROR",
      error instanceof Error ? error.message : "Network request failed"
    )
  }
}

export async function get<T>(endpoint: string, token?: string): Promise<T> {
  return apiClient<T>(endpoint, { method: "GET", token })
}

export async function post<T>(
  endpoint: string,
  data: unknown,
  token?: string
): Promise<T> {
  return apiClient<T>(endpoint, {
    method: "POST",
    body: JSON.stringify(data),
    token,
  })
}

export async function put<T>(
  endpoint: string,
  data: unknown,
  token?: string
): Promise<T> {
  return apiClient<T>(endpoint, {
    method: "PUT",
    body: JSON.stringify(data),
    token,
  })
}

export async function patch<T>(
  endpoint: string,
  data: unknown,
  token?: string
): Promise<T> {
  return apiClient<T>(endpoint, {
    method: "PATCH",
    body: JSON.stringify(data),
    token,
  })
}

export async function del<T>(
  endpoint: string,
  token?: string,
  body?: unknown,
): Promise<T> {
  return apiClient<T>(endpoint, {
    method: "DELETE",
    token,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  })
}

/**
 * POST a multipart/form-data body. apiClient() already omits the
 * Content-Type header for FormData bodies so the browser sets the
 * multipart boundary itself.
 */
export async function postForm<T>(
  endpoint: string,
  formData: FormData,
  token?: string,
): Promise<T> {
  return apiClient<T>(endpoint, { method: "POST", body: formData, token })
}

/**
 * GET binary content (a file download) as a Blob. apiClient() coerces
 * non-JSON responses to text, which corrupts binary payloads, so file
 * downloads need their own path. Error shapes mirror apiClient().
 */
export async function getBlob(endpoint: string, token?: string): Promise<Blob> {
  const response = await fetch(buildApiUrl(endpoint), {
    method: "GET",
    headers: await getAuthHeader(token),
  })
  if (!response.ok) {
    let errorData: ApiErrorResponse | null = null
    if (response.headers.get("content-type")?.includes("application/json")) {
      try {
        errorData = findErrorEnvelope((await response.json()) as unknown)
      } catch {
        // Non-JSON error body — fall through to the generic message.
      }
    }
    const errorCode = errorData?.error?.code || "UNKNOWN_ERROR"
    // Same unrecoverable-401 boot as apiClient — a dead session must not
    // strand the user on a page where downloads silently fail.
    if (response.status === 401 && typeof window !== "undefined") {
      if (errorCode === "IDLE_TIMEOUT") {
        handleTerminalAuthLogout("idle_timeout")
      } else if (!token && TERMINAL_AUTH_CODES.has(errorCode)) {
        handleTerminalAuthLogout("session_expired")
      }
    }
    throw new ApiError(
      errorCode,
      errorData?.error?.message ||
        `API request failed with status ${response.status}`,
      errorData?.error?.details,
      response.status,
    )
  }
  return response.blob()
}

// Surface any downstream-build additions (interceptors, error-readers) through
// the canonical `@/lib/api/client` path, so consumers never import the slot
// directly. Empty re-export in OSS.
export * from "./client.extensions"
