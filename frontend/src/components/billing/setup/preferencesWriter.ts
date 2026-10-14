// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { UserPreferences } from "@/lib/api/users"

/**
 * Writes setup answers to preferences one save at a time, newest last.
 *
 * The preferences endpoint replaces the whole object, so every save carries a
 * full copy built on whatever the screen last loaded. Saving each answer the
 * moment it was given went wrong in two ways when answers came quickly — two
 * Continue clicks in a row is enough:
 *
 *   - The second save was built on the copy from BEFORE the first one landed,
 *     so it carried the old, empty checklist and wiped the answer the first
 *     save had just stored. Coming back found the step but no answer, and an
 *     unanswered checklist only has one screen, so they were back at the start.
 *   - The two saves were in flight together, so the older could land last and
 *     put them back a step.
 *
 * So: one save in flight at most, and every answer given this sitting rides on
 * every save after it. Answers that arrive while a save is running are folded
 * into the one that follows it, which is built on what the last save returned.
 * Nothing is sent until the saved preferences have loaded, because a full
 * replace built on nothing would erase every other preference they have.
 *
 * Plain TypeScript rather than hook state: the queue has to outlive renders,
 * and nothing on screen reads it.
 */
export type SavePreferences = (next: UserPreferences) => Promise<UserPreferences>

export interface PreferencesWriter {
  /** Remember these keys and save them, now or after the save in flight. */
  write(patch: Partial<UserPreferences>): void
  /**
   * The preferences as last loaded, and how to save them. The first loaded
   * preferences also release any writes held until then.
   */
  load(preferences: UserPreferences | undefined, save: SavePreferences): void
}

export function createPreferencesWriter(): PreferencesWriter {
  let save: SavePreferences | undefined
  let base: UserPreferences | undefined
  // Everything written this sitting. Laid over every save, so a save built on
  // a copy that predates an answer still carries it.
  let written: Partial<UserPreferences> = {}
  let unsaved = false
  let saving = false

  async function flush() {
    if (saving || !unsaved || !base || !save) return
    saving = true
    unsaved = false
    try {
      base = await save({ ...base, ...written })
    } catch {
      // The mutation owns reporting the failure. What was written stays in
      // `written` and goes out with the next save; retrying here would loop
      // against a server that is refusing.
    } finally {
      saving = false
    }
    void flush()
  }

  return {
    write(patch) {
      written = { ...written, ...patch }
      unsaved = true
      void flush()
    },
    load(preferences, nextSave) {
      save = nextSave
      if (!preferences) return
      base = preferences
      void flush()
    },
  }
}
