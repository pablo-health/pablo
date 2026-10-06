// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import { VisitTimesPanel } from "../VisitTimesPanel"
import { peopleWords } from "@/lib/peopleTerm"
import type { VisitTimes } from "@/types/visitTimes"

const mockTimes = vi.fn()

vi.mock("@/hooks/usePeopleTerm", () => ({ usePeopleTerm: () => peopleWords("clients") }))
vi.mock("@/hooks/useVisitTimes", () => ({
  useVisitTimes: () => ({ data: mockTimes() }),
  useConfirmPsychotherapyWindow: () => ({ mutate: vi.fn(), isPending: false, isError: false }),
}))
vi.mock("@/hooks/usePreferences", () => ({ useUserTimeZone: () => "America/New_York" }))

function times(overrides: Partial<VisitTimes> = {}): VisitTimes {
  return {
    started_at: "2026-10-06T14:00:00Z",
    ended_at: "2026-10-06T15:07:00Z",
    total_minutes: 67,
    recording_started_at: "2026-10-06T14:00:00Z",
    client_present_end_seconds: 3895,
    clinician_addendum_seconds: 130,
    psychotherapy: null,
    total_with_documentation_minutes: null,
    ...overrides,
  }
}

describe("VisitTimesPanel", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the visit's start, end and minutes, and when the client left", () => {
    mockTimes.mockReturnValue(times())
    render(<VisitTimesPanel sessionId="s1" />)

    expect(screen.getByTestId("visit-line")).toHaveTextContent(
      "Started 10:00 AM · Ended 11:07 AM · 67 min",
    )
    expect(screen.getByTestId("client-present-line")).toHaveTextContent(
      "Client present until 11:04 AM · Your dictated addendum: 2 min",
    )
  })

  it("shows total time with documentation only when there is no psychotherapy section", () => {
    mockTimes.mockReturnValue(times({ total_with_documentation_minutes: 67 }))
    render(<VisitTimesPanel sessionId="s1" />)

    expect(screen.getByTestId("documentation-total")).toHaveTextContent(
      "Total time on this date, including documentation: 67 min",
    )
  })

  it("offers no psychotherapy window for a dictation", () => {
    mockTimes.mockReturnValue(
      times({
        client_present_end_seconds: 0,
        psychotherapy: {
          offered: false,
          end_seconds: null,
          turns: [],
          candidates: [],
          stated_clock_time: null,
          confirmed_start_seconds: null,
          confirmed_minutes: null,
          window_text: null,
          dictated_time: null,
          disagrees: false,
        },
      }),
    )
    render(<VisitTimesPanel sessionId="s1" />)

    expect(screen.getByTestId("client-present-line")).toHaveTextContent("Dictation only, 2 min")
    expect(screen.queryByTestId("psychotherapy-window")).not.toBeInTheDocument()
  })
})
