// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * API Client
 *
 * Centralized HTTP client for backend API communication.
 * Handles authentication, error handling, and type-safe responses.
 */

import { getFirebaseAuth } from "@/lib/firebase"

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
 * Make an authenticated API request
 *
 * Client-side: gets token from Firebase Auth current user
 * Server-side: token must be passed explicitly via the `token` option
 */
export async function apiClient<T>(
  endpoint: string,
  options: FetchOptions = {}
): Promise<T> {
  const { token, ...fetchOptions } = options

  const url = `${getApiUrl()}${endpoint}`

  const headers: Record<string, string> = {
    // Don't set Content-Type for FormData — browser must set it with multipart boundary
    ...(fetchOptions.body instanceof FormData
      ? {}
      : { "Content-Type": "application/json" }),
    ...(fetchOptions.headers as Record<string, string>),
  }

  // Get auth token - use provided token or get from Firebase client SDK
  let authToken = token
  if (!authToken && typeof window !== "undefined") {
    try {
      const currentUser = getFirebaseAuth().currentUser
      if (currentUser) {
        authToken = await currentUser.getIdToken()
      }
    } catch {
      // Firebase not initialized or no current user — proceed without token
    }
  }

  if (authToken) {
    headers["Authorization"] = `Bearer ${authToken}`
  }

  try {
    const response = await fetch(url, {
      ...fetchOptions,
      headers,
    })

    if (response.ok) {
      const contentType = response.headers.get("content-type")
      if (contentType?.includes("application/json")) {
        return (await response.json()) as T
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
