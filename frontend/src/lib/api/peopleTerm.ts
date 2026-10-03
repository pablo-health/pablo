// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { PeopleTerm } from "@/lib/peopleTerm"
import { get, put } from "./client"

/** The word in use for the signed-in clinician, and what decided it. */
export interface PeopleTermState {
  people_term: PeopleTerm
  /** The clinician's own choice; null when they have not made one. */
  choice: PeopleTerm | null
  /** The practice default; null when the practice has not set one. */
  practice_default: PeopleTerm | null
  can_set_practice_default: boolean
}

export function getPeopleTerm(token?: string): Promise<PeopleTermState> {
  return get<PeopleTermState>("/api/users/me/people-term", token)
}

/** Set the clinician's own word, or null to go back to the default. */
export function setPeopleTerm(term: PeopleTerm | null, token?: string): Promise<PeopleTermState> {
  return put<PeopleTermState>("/api/users/me/people-term", { people_term: term }, token)
}

/** Set the practice default (practice owner only), or null to clear it. */
export function setPracticePeopleTerm(
  term: PeopleTerm | null,
  token?: string,
): Promise<PeopleTermState> {
  return put<PeopleTermState>("/api/users/me/practice/people-term", { people_term: term }, token)
}
