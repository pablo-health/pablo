// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { calendarInSentence, calendarTitle, isMainCalendar } from "../calendarNames"
import { alreadyComingIn } from "../CalendarClientsStep"
import { calendarOptionLabel } from "../FollowCalendarPicker"

const MAIN = { id: "me@example.test", name: "me@example.test", primary: true }
const ALIAS = { id: "primary", name: "me@example.test", primary: false }
const SHARED = { id: "someone@example.test", name: "someone@example.test", primary: false }
const TEAM = { id: "team@group.calendar.google.test", name: "Group practice", primary: false }

describe("calendar names", () => {
  it("knows the main calendar by its flag or the primary alias, not by its name", () => {
    expect(isMainCalendar(MAIN)).toBe(true)
    expect(isMainCalendar(ALIAS)).toBe(true)
    // Someone else's calendar shared with the clinician is named after an
    // address too, and keeps that name.
    expect(isMainCalendar(SHARED)).toBe(false)
    expect(isMainCalendar(TEAM)).toBe(false)
  })

  it("calls the main calendar 'your main calendar' in a sentence and 'Main calendar' alone", () => {
    expect(calendarInSentence(MAIN)).toBe("your main calendar")
    expect(calendarTitle(MAIN)).toBe("Main calendar")
    expect(calendarOptionLabel(MAIN)).toBe("Main calendar")
  })

  it("keeps other calendars' own names", () => {
    expect(calendarInSentence(TEAM)).toBe("Group practice")
    expect(calendarTitle(SHARED)).toBe("someone@example.test")
    expect(calendarOptionLabel({ ...TEAM, made_by_pablo: true })).toBe(
      "Group practice (another Pablo setup)"
    )
  })

  it("says which calendar is already being imported", () => {
    expect(alreadyComingIn(MAIN)).toBe("Already importing your main calendar.")
    expect(alreadyComingIn(TEAM)).toBe("Already importing “Group practice”.")
    expect(alreadyComingIn(undefined)).toBe("Already importing your main calendar.")
  })
})
