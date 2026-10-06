// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import {
  addOnBand,
  clientPresentLineText,
  minutesBetween,
  placeStatedTime,
} from "../visitTimes"

const TZ = "America/New_York"
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

describe("minutesBetween", () => {
  it("rounds down", () => {
    expect(minutesBetween(750, 750 + 38 * 60 - 1)).toBe(37)
    expect(minutesBetween(750, 750 + 38 * 60)).toBe(38)
  })
})

describe("clientPresentLineText", () => {
  it("shows when the client left and how long the clinician dictated after", () => {
    expect(
      clientPresentLineText(
        { started_at: STARTED, client_present_end_seconds: 2158.4, clinician_addendum_seconds: 185.6 },
        TZ,
      ),
    ).toBe("Client present until 10:35 AM · Your dictated addendum: 3 min")
  })

  it("counts client-present minutes when the recording has no start time", () => {
    expect(
      clientPresentLineText(
        { started_at: null, client_present_end_seconds: 2158.4, clinician_addendum_seconds: 30 },
        TZ,
      ),
    ).toBe("Client present for 35 min")
  })

  it("calls a recording with no client a dictation", () => {
    expect(
      clientPresentLineText(
        { started_at: STARTED, client_present_end_seconds: 0, clinician_addendum_seconds: 130 },
        TZ,
      ),
    ).toBe("Dictation only, 2 min")
  })

  it("says nothing when the boundary is unknown", () => {
    expect(
      clientPresentLineText(
        { started_at: STARTED, client_present_end_seconds: null, clinician_addendum_seconds: null },
        TZ,
      ),
    ).toBeNull()
  })
})

describe("placeStatedTime", () => {
  it("places a stated time on the recording in the clinician's time zone", () => {
    expect(placeStatedTime("around 10:15", STARTED, TZ, 3600)).toBe(15 * 60)
    expect(placeStatedTime("10:15am", STARTED, TZ, 3600)).toBe(15 * 60)
  })

  it("does not place a time outside the client-present span", () => {
    expect(placeStatedTime("9:30", STARTED, TZ, 3600)).toBeNull()
    expect(placeStatedTime("11:30", STARTED, TZ, 3600)).toBeNull()
    expect(placeStatedTime("10:15 pm", STARTED, TZ, 3600)).toBeNull()
  })

  it("cannot place a time without the recording's start", () => {
    expect(placeStatedTime("10:15", null, TZ, 3600)).toBeNull()
  })
})
