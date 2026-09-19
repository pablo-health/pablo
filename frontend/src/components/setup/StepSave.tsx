// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from "react"
import type { ReactNode } from "react"

/**
 * Lets a wizard's Continue commit the form standing on the step.
 *
 * A setup step borrows the card Settings already uses rather than building a
 * second form against the same fields — the right call, and it brought one
 * assumption with it that does not survive the move. On a settings page the
 * card's own Save is the only way out, so "nothing is saved until you press
 * Save" is a complete instruction. Inside a wizard there is a bigger, primary
 * button directly below it that reads Continue, and that is the one a person
 * presses. Everything typed on the step was then dropped on the way to the
 * next one, silently, and turned up missing in Settings days later.
 *
 * So a card mounted inside a wizard registers its save here, and the step's
 * Continue runs it first. A card mounted anywhere else finds no provider and
 * registers nothing, which is why Settings behaves exactly as it did.
 *
 * SAVE FAILURES STOP THE STEP. `saveAll` rejects if any registered save
 * rejects, and the caller stays put — the card is already showing its own
 * error, and walking someone past a tax ID the server refused is how they
 * reach the ending believing it was accepted.
 */

interface StepSaveEntry {
  /** Resolves once the change has landed; rejects if it did not. */
  save: () => Promise<void>
  /** Registered saves are only run when there is something to save. */
  isDirty: boolean
}

type EntryRef = { current: StepSaveEntry }

interface StepSaveValue {
  register: (id: string, entry: EntryRef) => void
  unregister: (id: string) => void
  /** Run every dirty registered save. Rejects if any of them does. */
  saveAll: () => Promise<void>
}

const StepSaveContext = createContext<StepSaveValue | null>(null)

export function StepSaveProvider({ children }: { children: ReactNode }) {
  // Refs rather than state: registering must not re-render the wizard, and
  // what is registered is a closure over the card's current fields, which is a
  // new function on every render. Holding the card's ref object means the
  // wizard always calls the latest one without re-registering per keystroke.
  const entries = useRef(new Map<string, EntryRef>())

  const register = useCallback((id: string, entry: EntryRef) => {
    entries.current.set(id, entry)
  }, [])

  const unregister = useCallback((id: string) => {
    entries.current.delete(id)
  }, [])

  const saveAll = useCallback(async () => {
    const pending = [...entries.current.values()]
      .filter((entry) => entry.current.isDirty)
      .map((entry) => entry.current.save())
    await Promise.all(pending)
  }, [])

  const value = useMemo(() => ({ register, unregister, saveAll }), [register, unregister, saveAll])

  return <StepSaveContext.Provider value={value}>{children}</StepSaveContext.Provider>
}

/**
 * What a wizard uses to commit the step before leaving it.
 *
 * Resolves immediately outside a provider, so a screen with no registered form
 * is not a special case at the call site.
 */
export function useStepSave(): () => Promise<void> {
  const context = useContext(StepSaveContext)
  return useMemo(() => context?.saveAll ?? (() => Promise.resolve()), [context])
}

/**
 * Offer this form's save to whatever wizard is around it. A no-op elsewhere.
 *
 * `id` distinguishes two forms on one step; it is not persisted anywhere.
 */
export function useRegisterStepSave(id: string, save: () => Promise<void>, isDirty: boolean): void {
  const context = useContext(StepSaveContext)
  const entry = useRef<StepSaveEntry>({ save, isDirty })

  // After EVERY render, deliberately without a dependency list, so the wizard
  // never holds a stale closure over fields the person is still typing into.
  // Writing the ref during render instead would be the same intent and is what
  // `react-hooks/refs` exists to stop.
  useEffect(() => {
    entry.current = { save, isDirty }
  })

  useEffect(() => {
    if (!context) return
    context.register(id, entry)
    // Unregistering on unmount matters: a step left behind must not have its
    // save run by the next step's Continue.
    return () => context.unregister(id)
  }, [context, id])
}
