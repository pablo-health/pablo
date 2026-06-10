// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * API Client
 *
 * Centralized HTTP client for backend API communication.
 * Handles authentication, error handling, and type-safe responses.
 */

import { getClientAuthProvider } from "@/lib/auth/provider"

let idleTimeoutLogoutInFlight = false

function handleIdleTimeoutLogout() {
  if (idleTimeoutLogoutInFlight) return
  idleTimeoutLogoutInFlight = true
  void (async () => {
    try {
      // Provider sign-out clears both the client SDK session and the
      // server cookie (best-effort on each).
      await getClientAuthProvider().signOut()
    } catch {
      // Provider not initialized — still redirect.
    }
    window.location.assign("/login?reason=idle_timeout")
  })()
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

export class ApiError extends Error {
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
    const data = (await response.clone().json()) as ApiErrorResponse
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
        errorData = (await response.json()) as ApiErrorResponse
      } catch {
        // Failed to parse error response
      }
    }

    const errorCode = errorData?.error?.code || "UNKNOWN_ERROR"
    const errorMessage =
      errorData?.error?.message ||
      `API request failed with status ${response.status}`
    const errorDetails = errorData?.error?.details

    // Backend-enforced idle timeout: sign out and redirect so the user
    // sees the same /login?reason=idle_timeout flow as the client-side
    // IdleTimeout component. Guarded against re-entry so a parallel
    // burst of 401s doesn't fire multiple sign-outs / redirects.
    if (
      response.status === 401 &&
      errorCode === "IDLE_TIMEOUT" &&
      typeof window !== "undefined"
    ) {
      handleIdleTimeoutLogout()
    }

    throw new ApiError(errorCode, errorMessage, errorDetails, response.status)
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
        errorData = (await response.json()) as ApiErrorResponse
      } catch {
        // Non-JSON error body — fall through to the generic message.
      }
    }
    throw new ApiError(
      errorData?.error?.code || "UNKNOWN_ERROR",
      errorData?.error?.message ||
        `API request failed with status ${response.status}`,
      errorData?.error?.details,
      response.status,
    )
  }
  return response.blob()
}
