// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import { peopleWords } from "../peopleTerm"
import { addOnBand, clientPresentLineText, durationsLineText } from "../visitTimes"
import type { PsychotherapyWindow } from "@/types/visitTimes"

const TZ = "America/New_York"
const CLIENTS = peopleWords("clients")
// 10:00 AM in New York.
const STARTED = "2026-10-06T14:00:00Z"

describe("addOnBand", () => {
  it.each([
    [15, "Under the add-on minimum"],
    [16, "16–37 minutes"],
    [37, "16–37 minutes"],
    [38, "38–52 minutes"],
    [52, "38–52 minutes"],
    [53, "53 minutes or more"],
  ])("%i minutes is %s", (minutes, band) => {
    expect(addOnBand(minutes)).toBe(band)
  })
})

describe("clientPresentLineText", () => {
  it("shows when the client left and how long the clinician dictated after", () => {
    expect(
      clientPresentLineText(
        { started_at: STARTED, client_present_end_seconds: 2158.4, clinician_addendum_seconds: 185.6 },
        TZ,
        CLIENTS,
      ),
    ).toBe("Client present until 10:35 AM · Your dictated addendum: 3 min")
  })

  it("counts client-present minutes when the recording has no start time", () => {
    expect(
      clientPresentLineText(
        { started_at: null, client_present_end_seconds: 2158.4, clinician_addendum_seconds: 30 },
        TZ,
        CLIENTS,
      ),
    ).toBe("Client present for 35 min")
  })

  it("calls a recording with no client a dictation", () => {
    expect(
      clientPresentLineText(
        { started_at: STARTED, client_present_end_seconds: 0, clinician_addendum_seconds: 130 },
        TZ,
        CLIENTS,
      ),
    ).toBe("Dictation only, 2 min")
  })

  it("says nothing when the boundary is unknown", () => {
    expect(
      clientPresentLineText(
        { started_at: STARTED, client_present_end_seconds: null, clinician_addendum_seconds: null },
        TZ,
        CLIENTS,
      ),
    ).toBeNull()
  })
})

describe("durationsLineText", () => {
  const therapy = { offered: true, confirmed_minutes: 26 } as PsychotherapyWindow

  it("states the whole visit and the therapy alone", () => {
    expect(durationsLineText({ total_minutes: 52, psychotherapy: therapy })).toBe(
      "Total duration: 52 min · Psychotherapy duration: 26 min",
    )
  })

  it("says nothing until the therapy minutes are confirmed", () => {
    expect(
      durationsLineText({ total_minutes: 52, psychotherapy: { ...therapy, confirmed_minutes: null } }),
    ).toBeNull()
    expect(durationsLineText({ total_minutes: 52, psychotherapy: null })).toBeNull()
  })
})
