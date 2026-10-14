// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { PreferenceChanges, UserPreferences } from "@/lib/api/users"

/**
 * Writes setup answers to preferences one save at a time, newest last.
 *
 * Each save sends only the keys it changes, and the server merges them into
 * what is stored, so two saves in flight together can no longer erase each
 * other's answers. What is left is order: Continue twice in a row is two saves
 * of the same key (the step reached), and if they were in flight together the
 * older could land last and put them back a step. So there is one save in
 * flight at most, and answers given while it runs are folded into the one
 * that follows it.
 *
 * A refused save keeps its keys; they go out with the next save rather than
 * being retried here, which would loop against a server that is refusing.
 *
 * Plain TypeScript rather than hook state: the queue has to outlive renders,
 * and nothing on screen reads it.
 */
export type SavePreferences = (changes: PreferenceChanges) => Promise<UserPreferences>

export interface PreferencesWriter {
  /** Save these keys, now or after the save in flight. */
  write(changes: PreferenceChanges): void
  /** How to save. Saves written before this is first called wait for it. */
  setSave(save: SavePreferences): void
}

export function createPreferencesWriter(): PreferencesWriter {
  let save: SavePreferences | undefined
  let unsaved: PreferenceChanges = {}
  let saving = false

  async function flush() {
    if (saving || !save || Object.keys(unsaved).length === 0) return
    saving = true
    const sending = unsaved
    unsaved = {}
    try {
      await save(sending)
    } catch {
      // The mutation owns reporting the failure. Anything written since this
      // save began is newer, so it wins over what is put back.
      const writtenSince = Object.keys(unsaved).length > 0
      unsaved = { ...sending, ...unsaved }
      if (!writtenSince) {
        saving = false
        return
      }
    }
    saving = false
    void flush()
  }

  return {
    write(changes) {
      unsaved = { ...unsaved, ...changes }
      void flush()
    },
    setSave(nextSave) {
      save = nextSave
      void flush()
    },
  }
}
