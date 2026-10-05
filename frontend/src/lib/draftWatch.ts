// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The sessions whose drafts the app is waiting on.
 *
 * Two things put a session here: a transcript upload that just started one
 * (so a draft that lands within a second of the upload is still caught), and
 * the session list showing one mid-draft. `useDraftNotices` polls each until
 * its draft lands, then takes it off.
 */

import { useSyncExternalStore } from "react"

const watched = new Set<string>()
const listeners = new Set<() => void>()
const NONE: readonly string[] = []
let snapshot: readonly string[] = NONE

function publish(): void {
  snapshot = [...watched]
  listeners.forEach((listener) => listener())
}

export function watchDraft(sessionId: string): void {
  if (watched.has(sessionId)) return
  watched.add(sessionId)
  publish()
}

export function unwatchDraft(sessionId: string): void {
  if (!watched.delete(sessionId)) return
  publish()
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useWatchedDrafts(): readonly string[] {
  return useSyncExternalStore(
    subscribe,
    () => snapshot,
    () => NONE,
  )
}

/** Tests only: forget every watched session. */
export function resetWatchedDrafts(): void {
  watched.clear()
  publish()
}
