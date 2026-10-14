// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Marks an empty field or section while a note is being read. It is a cue on
 * the screen, not part of the note: the PDF leaves the empty field out, and
 * the cue can't be selected, so copying the note doesn't pick it up.
 */
export function NothingEntered() {
  return <p className="text-sm text-neutral-400 select-none">Nothing entered</p>
}
