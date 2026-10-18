// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every string the chart's Homework tab shows a clinician, in one place.
 *
 * Suggestions from a signed note are chart updates and use that surface's
 * words; this tab is the assignments. The portal line is shown only when
 * the practice's portal settings say homework is on.
 */

export const EDIT = "Edit"
export const SAVE = "Save"
export const CANCEL = "Cancel"
export const REMOVE = "Remove"

export const EMPTY = "No homework assigned."
export const NOT_DONE = "Not done yet"
export const ADD_LABEL = "Add homework"
export const ADD_PLACEHOLDER = "Keep a sleep diary each night"
export const TEXT_REQUIRED = "Write what to do before you add it."
export const LOAD_FAILED = "Homework isn't loading right now. Try again in a moment."
export const SAVE_FAILED = "That didn't save. Try again."

export function doneOn(date: string): string {
  return `Done ${date}`
}

export function progress(done: number, total: number): string {
  return `${done} of ${total} done`
}

/** Shown only when the portal and its homework part are both on. */
export function portalLine(peopleMany: string): string {
  return `${peopleMany} see assigned homework in the portal.`
}
