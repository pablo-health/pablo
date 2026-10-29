// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every string the homework section shows a client, in one place.
 *
 * Second person throughout, and nothing about how an item got here: the
 * client sees what their clinician assigned, not that a note was read.
 * "Done" is the client's own word for it; nothing here says anyone has
 * seen it yet.
 */

export const HEADING = "Homework"
export const EMPTY = "Nothing assigned right now."
export const MARK_DONE = "Mark done"
export const CONFIRM_DONE = "Mark done"
export const DONE = "Done"
export const CANCEL = "Cancel"
export const NOT_DONE_YET = "Not done yet"
export const NOTE_LABEL = "Anything to tell your clinician? (optional)"
export const LOAD_FAILED = "Homework isn't loading right now. Try again in a moment."
export const SAVE_FAILED = "That didn't save. Try again."

export function assignedOn(date: string): string {
  return `Assigned ${date}`
}

export function doneOn(date: string): string {
  return `Done ${date}`
}

/** The home-screen tile's line. */
export function summaryLine(open: number, done: number): string {
  if (open === 0 && done === 0) return "Nothing assigned"
  if (open === 0) return "All done"
  return open === 1 ? "1 to do" : `${open} to do`
}
