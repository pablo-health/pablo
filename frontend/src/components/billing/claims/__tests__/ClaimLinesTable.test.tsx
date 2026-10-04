// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { render, screen } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import { peopleWords } from "@/lib/peopleTerm"
import { ClaimLinesTable } from "../ClaimLinesTable"
import { line } from "./claimFixtures"

vi.mock("@/hooks/usePeopleTerm", async (orig) => ({
  ...(await orig<typeof import("@/hooks/usePeopleTerm")>()),
  usePeopleTerm: () => peopleWords("patients"),
}))

describe("ClaimLinesTable", () => {
  it("names the person's share in the clinician's own word", () => {
    render(<ClaimLinesTable lines={[line()]} adjudicated />)
    expect(screen.getByText("Patient owes")).toBeInTheDocument()
    expect(screen.queryByText("Client owes")).not.toBeInTheDocument()
  })
})
