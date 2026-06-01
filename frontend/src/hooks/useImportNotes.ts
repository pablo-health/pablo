// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * useImportNotes
 *
 * Orchestrates a bulk import of existing SOAP-note documents for one patient.
 * Each file is imported independently via the single-file import endpoint, so
 * one unreadable file never sinks the batch. Files run a few at a time and the
 * hook exposes per-file progress for the dialog to render. When the run
 * finishes it invalidates the session and patient lists so the Review queue
 * and patient chart reflect the new imports.
 */

"use client"

import { useCallback, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { importNote } from "@/lib/api/sessions"
import { queryKeys } from "@/lib/api/queryKeys"
import type { SessionResponse } from "@/types/sessions"

export type ImportItemStatus = "queued" | "parsing" | "done" | "error"

export interface ImportItem {
  /** Stable key for React lists (file name may repeat across a batch). */
  id: string
  file: File
  status: ImportItemStatus
  /** User-facing error message when status is "error". */
  error?: string
  /** The created session when status is "done". */
  session?: SessionResponse
}

/** How many files import at once. Modest, to be gentle on the parse backend. */
const CONCURRENCY = 3

export interface UseImportNotesResult {
  items: ImportItem[]
  isRunning: boolean
  /** True once a run has finished (any items present and none in flight). */
  isComplete: boolean
  doneCount: number
  errorCount: number
  /** Import the given files (replaces any prior run). */
  start: (files: File[]) => Promise<void>
  /** Clear all state back to empty (e.g. when the dialog closes). */
  reset: () => void
}

export function useImportNotes(
  patientId: string,
  token?: string,
): UseImportNotesResult {
  const queryClient = useQueryClient()
  const [items, setItems] = useState<ImportItem[]>([])
  const [isRunning, setIsRunning] = useState(false)
  const seq = useRef(0)

  const patchItem = useCallback(
    (id: string, patch: Partial<ImportItem>) => {
      setItems((prev) =>
        prev.map((it) => (it.id === id ? { ...it, ...patch } : it)),
      )
    },
    [],
  )

  const start = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return

      const batch: ImportItem[] = files.map((file) => ({
        id: `${seq.current++}-${file.name}`,
        file,
        status: "queued",
      }))
      setItems(batch)
      setIsRunning(true)

      // Simple concurrency pool: a shared cursor that each worker advances.
      // Safe without locking — JS is single-threaded and the increment is
      // synchronous between awaits.
      let cursor = 0
      const runWorker = async () => {
        while (cursor < batch.length) {
          const item = batch[cursor++]
          patchItem(item.id, { status: "parsing" })
          try {
            const session = await importNote(patientId, item.file, { token })
            patchItem(item.id, { status: "done", session })
          } catch (err) {
            patchItem(item.id, {
              status: "error",
              error:
                err instanceof Error
                  ? err.message
                  : "Couldn't import this file.",
            })
          }
        }
      }

      await Promise.all(
        Array.from({ length: Math.min(CONCURRENCY, batch.length) }, runWorker),
      )

      setIsRunning(false)
      // New sessions affect the Review queue; the import also bumps the
      // patient's session count / last-session date.
      void queryClient.invalidateQueries({ queryKey: queryKeys.sessions.all })
      void queryClient.invalidateQueries({ queryKey: queryKeys.patients.all })
    },
    [patientId, token, patchItem, queryClient],
  )

  const reset = useCallback(() => {
    setItems([])
    setIsRunning(false)
  }, [])

  const doneCount = items.filter((it) => it.status === "done").length
  const errorCount = items.filter((it) => it.status === "error").length
  const isComplete =
    items.length > 0 && !isRunning && doneCount + errorCount === items.length

  return { items, isRunning, isComplete, doneCount, errorCount, start, reset }
}
