// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"

import { peopleWords } from "@/lib/peopleTerm"
import { clientPresentLineText } from "../ClientPresentLine"

const TZ = "America/New_York"
const CLIENTS = peopleWords("clients")

describe("clientPresentLineText", () => {
  it("shows when the client left and how long the clinician dictated after", () => {
    expect(
      clientPresentLineText(
        {
          started_at: "2026-10-06T14:00:00Z",
          client_present_end_seconds: 2158.4,
          clinician_addendum_seconds: 185.6,
        },
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

  it("uses the practice's word for the people it sees", () => {
    expect(
      clientPresentLineText(
        { started_at: null, client_present_end_seconds: 2158.4, clinician_addendum_seconds: 0 },
        TZ,
        peopleWords("patients"),
      ),
    ).toBe("Patient present for 35 min")
  })

  it("calls a recording with no client a dictation", () => {
    expect(
      clientPresentLineText(
        {
          started_at: "2026-10-06T14:00:00Z",
          client_present_end_seconds: 0,
          clinician_addendum_seconds: 130,
        },
        TZ,
        CLIENTS,
      ),
    ).toBe("Dictation only, 2 min")
  })

  it("says nothing when the boundary is unknown", () => {
    expect(
      clientPresentLineText({ started_at: "2026-10-06T14:00:00Z", client_present_end_seconds: null }, TZ, CLIENTS),
    ).toBeNull()
  })
})
