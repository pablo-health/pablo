// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Bearer-token client for "given X" setup calls, plus the two emulator REST
 * calls a test user needs: create the account and mint an id token for it.
 *
 * The emulator's REST surface is the production one at a local host; the
 * `key` query parameter is required by shape and ignored by value.
 */

import { AUTH_EMULATOR_URL, BACKEND_URL } from "./stack"

const IDENTITY_TOOLKIT = `${AUTH_EMULATOR_URL}/identitytoolkit.googleapis.com/v1`

interface SignUpResponse {
  localId: string
  idToken: string
}

interface SignInResponse {
  idToken: string
}

async function identityToolkit<T>(
  method: string,
  body: Record<string, unknown>,
  headers: Record<string, string> = {},
): Promise<T> {
  const response = await fetch(`${IDENTITY_TOOLKIT}/${method}?key=e2e`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`emulator ${method} failed: ${response.status} ${await response.text()}`)
  }
  return (await response.json()) as T
}

/** Create a verified email/password user on the emulator. */
export async function createEmulatorUser(email: string, password: string): Promise<{ uid: string }> {
  const created = await identityToolkit<SignUpResponse>("accounts:signUp", {
    email,
    password,
    returnSecureToken: true,
  })
  // `Bearer owner` is the emulator's stand-in for an admin credential; a
  // verified address keeps every "please verify your email" branch out of
  // the way so the suite exercises sign-in, not mail.
  await identityToolkit(
    "accounts:update",
    { localId: created.localId, emailVerified: true },
    { Authorization: "Bearer owner" },
  )
  return { uid: created.localId }
}

/** An id token for an existing emulator user, for direct API calls. */
export async function signInWithPassword(email: string, password: string): Promise<string> {
  const signedIn = await identityToolkit<SignInResponse>("accounts:signInWithPassword", {
    email,
    password,
    returnSecureToken: true,
  })
  return signedIn.idToken
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly method: string,
    readonly path: string,
    readonly body: string,
  ) {
    super(`${method} ${path} → ${status}: ${body}`)
  }
}

/**
 * Minimal JSON client against the API, authenticated as one test user.
 * Used by the scenario helpers to put the practice into a known state
 * before a spec drives the browser.
 */
export class ApiClient {
  constructor(
    private readonly token: string,
    readonly baseUrl: string = BACKEND_URL,
  ) {}

  static async forUser(email: string, password: string): Promise<ApiClient> {
    return new ApiClient(await signInWithPassword(email, password))
  }

  async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const response = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${this.token}`,
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
    if (!response.ok) {
      throw new ApiError(response.status, method, path, await response.text())
    }
    if (response.status === 204) return undefined as T
    return (await response.json()) as T
  }

  get<T>(path: string): Promise<T> {
    return this.request<T>("GET", path)
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body)
  }

  patch<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("PATCH", path, body)
  }

  delete(path: string): Promise<void> {
    return this.request<void>("DELETE", path)
  }
}
