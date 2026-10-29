// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Runs once when the server starts, before it serves a request.
 *
 * Registers the source of the end user's IP and agent for the backend calls
 * this server makes while rendering a page (see `src/lib/api/clientForwarding.ts`).
 * Node.js runtime only: the module reads the incoming request with
 * `next/headers` and a server-only secret.
 */
export async function register(): Promise<void> {
  if (process.env.NEXT_RUNTIME !== "nodejs") return
  const { registerForwardedClientHeaders } = await import("./src/lib/api/clientForwarding")
  const { forwardedClientHeadersForRequest } = await import(
    "./src/lib/api/clientForwarding.server"
  )
  registerForwardedClientHeaders(forwardedClientHeadersForRequest)
}
