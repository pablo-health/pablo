// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether the app says "clients" or "patients" to a clinician.
 *
 * Therapists usually say clients; prescribers usually say patients. The
 * backend decides which one a clinician sees (their own choice, then the
 * practice default, then "clients") and every screen a
 * clinician reads takes the word from `usePeopleTerm()` rather than spelling
 * it out. `peopleTermGuard.test.ts` fails on a new hard-coded one.
 *
 * Only the words change. URLs, API paths and identifiers stay "patient".
 */

export type PeopleTerm = "clients" | "patients"

export const DEFAULT_PEOPLE_TERM: PeopleTerm = "clients"

/** The word in each form a sentence needs: `one`/`many` lower case, `One`/`Many` capitalised. */
export interface PeopleWords {
  term: PeopleTerm
  one: string
  many: string
  One: string
  Many: string
}

const capitalise = (word: string) => word.charAt(0).toUpperCase() + word.slice(1)

export function peopleWords(term: PeopleTerm): PeopleWords {
  const many = term
  const one = term.slice(0, -1)
  return { term, one, many, One: capitalise(one), Many: capitalise(many) }
}
