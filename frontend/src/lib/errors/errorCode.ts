// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Reduce a caught error to a short code safe to hand to console.error or
 * fold into a user-facing message — never the error object itself, which
 * can carry a full request/response payload.
 */
export function errorCode(err: unknown): string {
  return (err as { code?: string })?.code || "unknown"
}
