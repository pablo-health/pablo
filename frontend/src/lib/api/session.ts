// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Session-liveness API. Backs the IdleTimeout controller: the peek asks
 * the backend "is this session still alive?" without extending it, the
 * touch explicitly extends it. See backend/app/auth/idle_session.py for
 * the enforcement model.
 */

import { get, post } from "./client"

export interface SessionStatus {
  /** False when server-side idle enforcement is off (dev mode / no
   * Redis) — the client falls back to its local activity clock. */
  enforced: boolean
  active: boolean
  seconds_remaining: number | null
}

/**
 * Read-only liveness check. Never refreshes the idle heartbeat — safe to
 * call on mount, tab restore, and on a poll.
 */
export function getSessionStatus(): Promise<SessionStatus> {
  return get<SessionStatus>("/api/auth/session")
}

/**
 * Explicit keep-alive: refresh the idle heartbeat. 401s (and boots to
 * /login via the apiClient terminal-auth handling) if the session has
 * already idled out.
 */
export function touchSession(): Promise<SessionStatus> {
  return post<SessionStatus>("/api/auth/session/touch", {})
}
