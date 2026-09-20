// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Given X" helpers for the portal's appointments module: the practice-side
 * setup a patient needs before there is anything to book.
 *
 * Signing in is NOT here. `portal.ts` owns the invite-and-redeem seam that
 * every portal spec shares, and a second copy of it would be a second thing
 * to keep in step with how invitations are delivered.
 */

import type { ApiClient } from "./api"
import { giveWorkingHours } from "./scenarios"

/**
 * The practice-wide switch for patients it already has.
 *
 * Off for every practice until somebody says otherwise, which is why a spec
 * that books has to turn it on and a spec that wants the closed state passes
 * `false`. Distinct from `self_book_new`, which governs a stranger booking a
 * first visit through a public link — everybody reaching the portal is
 * already a patient here, so that is a different question.
 */
export async function letExistingClientsSelfBook(
  api: ApiClient,
  allowed = true,
): Promise<void> {
  await api.patch("/api/scheduling/policy", { self_book_existing: allowed })
}

/**
 * Hours on every weekday, so a spec can walk forward to any day and find
 * openings rather than having to work out which weekday it is running on.
 *
 * One rule per weekday, because the engine accumulates rules and two
 * covering the same day would offer every slot twice.
 */
export async function giveWorkingHoursAllWeek(api: ApiClient): Promise<void> {
  for (let day = 0; day < 7; day += 1) {
    await giveWorkingHours(api, day)
  }
}

export interface SelfBookableType {
  id: string
  name: string
  duration_minutes: number
}

/**
 * An appointment type an existing patient may book for themselves.
 *
 * `self_bookable` is an allow-list rather than a deny-list: a practice that
 * flips the master switch without opting a type in has opened nothing, which
 * is the safe direction and the state the second spec here relies on.
 */
export async function giveSelfBookableType(
  api: ApiClient,
  name: string,
): Promise<SelfBookableType> {
  return api.post<SelfBookableType>("/api/appointment-types", {
    name,
    duration_minutes: 50,
    audience: "existing",
    self_bookable: true,
    offerable: true,
  })
}
