// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { peopleWords, sayPeople } from "@/lib/peopleTerm"

describe("peopleWords", () => {
  it("gives every form of each word", () => {
    expect(peopleWords("clients")).toMatchObject({ one: "client", many: "clients", One: "Client", Many: "Clients" })
    expect(peopleWords("patients")).toMatchObject({ one: "patient", many: "patients", One: "Patient", Many: "Patients" })
  })
})

describe("sayPeople", () => {
  it("fills each placeholder in the clinician's word", () => {
    expect(sayPeople("{People}: new {people}, one {person}, {Person} portal", peopleWords("patients"))).toBe(
      "Patients: new patients, one patient, Patient portal",
    )
  })

  it("leaves text without a placeholder alone", () => {
    expect(sayPeople("Supervision", peopleWords("clients"))).toBe("Supervision")
  })
})
