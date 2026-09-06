// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where the pieces of the stack under test listen, as seen from the machine
 * running the suite. Defaults match docker-compose.e2e.yml; the same
 * E2E_*_PORT variables override both sides.
 */

const port = (name: string, fallback: string): string => process.env[name] || fallback

export const BASE_URL = process.env.E2E_BASE_URL || `http://localhost:${port("E2E_FRONTEND_PORT", "3000")}`
export const BACKEND_URL =
  process.env.E2E_BACKEND_URL || `http://localhost:${port("E2E_BACKEND_PORT", "8000")}`
export const AUTH_EMULATOR_URL =
  process.env.E2E_AUTH_EMULATOR_URL || `http://localhost:${port("E2E_AUTH_EMULATOR_PORT", "9099")}`
export const CLEARINGHOUSE_URL =
  process.env.E2E_CLEARINGHOUSE_URL || `http://localhost:${port("E2E_CLEARINGHOUSE_PORT", "8080")}`

/** The emulator-only project the stack is configured with. */
export const FIREBASE_PROJECT_ID = "demo-pablo-e2e"
