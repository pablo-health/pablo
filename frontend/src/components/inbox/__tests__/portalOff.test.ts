// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import type { PortalSettings } from "@/lib/api/portalSettings"
import { peopleWords } from "@/lib/peopleTerm"
import { portalOffNotice, repliesOffNote } from "../portalOff"

const off = { enabled: false } as PortalSettings

describe("portalOff", () => {
  it("names the portal with the clinician's own word", () => {
    expect(portalOffNotice(off, peopleWords("clients"))).toBe("Your client portal is off.")
    expect(portalOffNotice(off, peopleWords("patients"))).toBe("Your patient portal is off.")
    expect(repliesOffNote(off, peopleWords("patients"))).toBe(
      "Turn the patient portal back on to reply.",
    )
  })
})
