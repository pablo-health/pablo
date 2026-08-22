// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Read-only deployment mode slot.
 *
 * Returns whether this deployment is serving the app view-only: the chart,
 * notes, documents and calendar all still read and export normally, but the
 * affordances that *open* a write flow are hidden. That covers a practice
 * winding down, an audit review window, and a migration freeze — cases where
 * the record must stay legible and exportable while nothing new is written.
 *
 * The base build reads a single deployment-level flag,
 * `NEXT_PUBLIC_READ_ONLY`; a downstream build overwrites *this file only* to
 * source the same boolean from its own runtime signal (it can call its own
 * hooks here, since every call site is a client component).
 *
 * Two rules the replacements must keep:
 *   * Fail open. While the answer is still loading, return `false` — the API
 *     is the enforcement boundary, and a flash of hidden buttons on every
 *     page load is a worse trade than a button that 403s in the rare window.
 *   * Never gate reads or exports. Record access is exactly what read-only
 *     mode exists to preserve.
 */
export function useReadOnlyMode(): { readOnly: boolean } {
  return { readOnly: process.env.NEXT_PUBLIC_READ_ONLY === "true" }
}
