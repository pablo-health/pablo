// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where the pieces of the stack under test listen, as seen from the machine
 * running the suite. Defaults match docker-compose.e2e.yml; the same
 * E2E_*_PORT variables override both sides.
 */

const port = (name: string, fallback: string): string => process.env[name] || fallback

export const BASE_URL = process.env.E2E_BASE_URL || `http://localhost:${port("E2E_FRONTEND_PORT", "3000")}`
/**
 * The portal's own host: the same frontend server under another name, which
 * docker-compose.e2e.yml names in the frontend's PORTAL_HOSTS.
 */
export const PORTAL_URL =
  process.env.E2E_PORTAL_URL || `http://127.0.0.1:${port("E2E_FRONTEND_PORT", "3000")}`
/**
 * The stand-in load balancer in front of practices' own hosts
 * (practice-host-lb in docker-compose.e2e.yml): reached under a practice's
 * hostname, it splits `/api` between the frontend and the backend as a
 * deployed balancer does.
 */
export const PRACTICE_HOST_PORT = port("E2E_PRACTICE_HOST_PORT", "3080")
export const BACKEND_URL =
  process.env.E2E_BACKEND_URL || `http://localhost:${port("E2E_BACKEND_PORT", "8000")}`
export const AUTH_EMULATOR_URL =
  process.env.E2E_AUTH_EMULATOR_URL || `http://localhost:${port("E2E_AUTH_EMULATOR_PORT", "9099")}`
export const CLEARINGHOUSE_URL =
  process.env.E2E_CLEARINGHOUSE_URL || `http://localhost:${port("E2E_CLEARINGHOUSE_PORT", "8080")}`
export const MAIL_URL = process.env.E2E_MAIL_URL || `http://localhost:${port("E2E_MAIL_PORT", "8025")}`
export const SMS_URL = process.env.E2E_SMS_URL || `http://localhost:${port("E2E_SMS_PORT", "8026")}`
/** The stand-in name server's HTTP hooks (scripts/fake_dns.py). */
export const DNS_URL = process.env.E2E_DNS_URL || `http://localhost:${port("E2E_DNS_PORT", "8027")}`
/**
 * The stand-in Google. One address for the browser (sent to its
 * authorization page) and the backend (which exchanges the code and reads
 * calendars), which is why it lives in the backend's network namespace.
 */
export const GOOGLE_URL =
  process.env.E2E_GOOGLE_URL || `http://localhost:${port("E2E_GOOGLE_PORT", "8090")}`

/** The emulator-only project the stack is configured with. */
export const FIREBASE_PROJECT_ID = "demo-pablo-e2e"
