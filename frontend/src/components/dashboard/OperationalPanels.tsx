// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useReadOnlyMode } from "@/lib/access/readOnlyMode"

/**
 * Wraps the dashboard's operational panels (today's sessions, the week
 * ahead, compliance reminders) and hides them in read-only mode.
 *
 * A read-only deployment serves the practice's records, not its
 * operations: nothing is scheduled "today", reminders aren't being
 * sent, and a compliance queue that can't be acted on reads as a wall
 * of stale obligations. The records themselves (patients, notes,
 * sessions, documents) stay fully browsable through their own pages.
 *
 * Client component wrapper because the dashboard page is a server
 * component and the read-only signal is a client hook.
 */
export function OperationalPanels({ children }: { children: React.ReactNode }) {
  const { readOnly } = useReadOnlyMode()
  if (readOnly) return null
  return <>{children}</>
}
