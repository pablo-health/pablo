// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * You > Away — setting away dates and reviewing the reply clients get.
 *
 * What these would catch: saving dates without the reply being shown first;
 * the reply switched off by default; the safety lines offered for editing or
 * missing; an edit that drops the reply the clinician already wrote when the
 * dates did not change; and ending a period sending anything but its id.
 * For coverage: a colleague shown as covering before they accept; a covered
 * period that is over without a way to see what the colleague did, or that
 * list hiding activity it has no label for; and an answer to a request
 * reaching the wrong period or the wrong verb.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import type {
  AwayPeriod,
  AwayPeriodList,
  CoverageActivity,
  CoverageRequestList,
} from "@/types/away"
import { AwayPage } from "../AwayPage"

const SAFETY = "If this is an emergency, call 911.\nFor the Suicide & Crisis Lifeline, call or text 988."
const SUGGESTED = "Thanks for your message. Robin Avery is away and back on Monday, November 2."

const list = vi.hoisted(() => ({ current: undefined as AwayPeriodList | undefined }))
const requests = vi.hoisted(() => ({ current: undefined as CoverageRequestList | undefined }))
const activity = vi.hoisted(() => ({ current: undefined as CoverageActivity | undefined }))
const preview = vi.hoisted(() => vi.fn())
const create = vi.hoisted(() => vi.fn())
const update = vi.hoisted(() => vi.fn())
const end = vi.hoisted(() => vi.fn())
const answer = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useAwayPeriods", () => ({
  useAwayPeriods: () => ({ data: list.current }),
  usePreviewAcknowledgment: () => ({ mutate: preview, isPending: false }),
  useCreateAwayPeriod: () => ({ mutate: create, isPending: false }),
  useUpdateAwayPeriod: () => ({ mutate: update, isPending: false }),
  useEndAwayPeriod: () => ({ mutate: end, isPending: false }),
  useCoverageActivity: () => ({ data: activity.current, isLoading: false }),
  useCoverageRequests: () => ({ data: requests.current }),
  useAnswerCoverageRequest: () => ({ mutate: answer, isPending: false, error: null }),
}))

const PERIOD: AwayPeriod = {
  id: "period-1",
  start_date: "2026-10-26",
  end_date: "2026-10-30",
  return_date: "2026-11-02",
  covering_user_id: null,
  covering_name: null,
  coverage_status: null,
  acknowledgment_enabled: true,
  acknowledgment_text: "My own words, back on Monday, November 2.",
  safety_lines: SAFETY,
  active: true,
  ended_at: null,
}

beforeEach(() => {
  vi.clearAllMocks()
  list.current = {
    periods: [],
    recently_covered: [],
    covering_options: [{ user_id: "colleague", name: "Sam Okafor" }],
  }
  preview.mockImplementation((_dates, { onSuccess }) =>
    onSuccess({ acknowledgment_text: SUGGESTED, safety_lines: SAFETY, return_date: "2026-11-02" }),
  )
})

function replyText(): string {
  return (screen.getByTestId("away-reply-text") as HTMLTextAreaElement).value
}

function enterDates() {
  fireEvent.click(screen.getByTestId("away-add"))
  fireEvent.change(screen.getByTestId("away-start"), { target: { value: "2026-10-26" } })
  fireEvent.change(screen.getByTestId("away-end"), { target: { value: "2026-10-30" } })
  fireEvent.click(screen.getByTestId("away-next"))
}

describe("AwayPage", () => {
  it("shows the reply for review, on, with the return date in it, before saving", () => {
    render(<AwayPage />)
    enterDates()

    expect(preview).toHaveBeenCalledWith(
      { start_date: "2026-10-26", end_date: "2026-10-30", return_date: null, covering_user_id: null },
      expect.anything(),
    )
    expect(screen.getByTestId("away-reply-text")).toHaveValue(SUGGESTED)
    expect(replyText()).toContain("Monday, November 2")
    expect(screen.getByRole("switch")).toHaveAttribute("aria-checked", "true")
    expect(create).not.toHaveBeenCalled()
  })

  it("shows the 911 and 988 lines and does not offer them for editing", () => {
    render(<AwayPage />)
    enterDates()

    const lines = screen.getByTestId("away-safety-lines")
    expect(lines.textContent).toContain("call 911")
    expect(lines.textContent).toContain("988")
    expect(lines.tagName).not.toBe("TEXTAREA")
    expect(lines.querySelector("input, textarea")).toBeNull()
    expect(replyText()).not.toContain("988")
  })

  it("saves the reviewed reply, and saves it switched off when it was switched off", () => {
    render(<AwayPage />)
    enterDates()
    fireEvent.change(screen.getByTestId("away-reply-text"), { target: { value: "Back soon." } })
    fireEvent.click(screen.getByRole("switch"))
    fireEvent.click(screen.getByTestId("away-save"))

    expect(create).toHaveBeenCalledWith(
      {
        start_date: "2026-10-26",
        end_date: "2026-10-30",
        return_date: "2026-11-02",
        covering_user_id: null,
        acknowledgment_enabled: false,
        acknowledgment_text: "Back soon.",
      },
      expect.anything(),
    )
  })

  it("keeps the reply already written when an edit leaves the dates alone", () => {
    list.current = { periods: [PERIOD], recently_covered: [], covering_options: [] }
    render(<AwayPage />)
    fireEvent.click(screen.getByRole("button", { name: "Edit" }))
    fireEvent.click(screen.getByTestId("away-next"))

    expect(preview).not.toHaveBeenCalled()
    expect(screen.getByTestId("away-reply-text")).toHaveValue(PERIOD.acknowledgment_text)
  })

  it("asks for a new suggestion when the dates change", () => {
    list.current = { periods: [PERIOD], recently_covered: [], covering_options: [] }
    render(<AwayPage />)
    fireEvent.click(screen.getByRole("button", { name: "Edit" }))
    fireEvent.change(screen.getByTestId("away-end"), { target: { value: "2026-10-29" } })
    fireEvent.click(screen.getByTestId("away-next"))

    expect(preview).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("away-reply-text")).toHaveValue(SUGGESTED)
  })

  it("ends a period by its id", () => {
    list.current = { periods: [PERIOD], recently_covered: [], covering_options: [] }
    render(<AwayPage />)
    fireEvent.click(screen.getByTestId("away-end-period-1"))

    expect(end).toHaveBeenCalledWith("period-1")
  })

  it("asks for both days before moving on", () => {
    render(<AwayPage />)
    fireEvent.click(screen.getByTestId("away-add"))
    fireEvent.click(screen.getByTestId("away-next"))

    expect(screen.getByRole("alert")).toHaveTextContent("Choose the first and last day you're away.")
    expect(preview).not.toHaveBeenCalled()
  })
})

describe("AwayPage coverage", () => {
  const ASKED: AwayPeriod = {
    ...PERIOD,
    covering_user_id: "colleague",
    covering_name: "Sam Okafor",
    coverage_status: "requested",
  }

  beforeEach(() => {
    requests.current = undefined
    activity.current = undefined
  })

  it.each([
    ["requested", "Waiting for Sam Okafor to accept"],
    ["accepted", "Sam Okafor covering"],
    ["declined", "Sam Okafor can't cover"],
  ] as const)("says where a %s request stands", (status, words) => {
    list.current = {
      periods: [{ ...ASKED, coverage_status: status }],
      recently_covered: [],
      covering_options: [],
    }
    render(<AwayPage />)

    expect(screen.getByTestId("away-coverage-period-1")).toHaveTextContent(words)
    if (status !== "accepted") {
      expect(screen.getByTestId("away-coverage-period-1")).not.toHaveTextContent("covering")
    }
  })

  it("lists what the colleague did once a covered period is over, with links", () => {
    list.current = {
      periods: [],
      recently_covered: [{ ...ASKED, coverage_status: "accepted", active: false }],
      covering_options: [],
    }
    activity.current = {
      covering_name: "Sam Okafor",
      items: [
        {
          at: "2026-10-28T14:00:00Z",
          action: "patient_viewed",
          resource_type: "patient",
          resource_id: "client-1",
          patient_id: "client-1",
          client_name: "Avery Client",
        },
        {
          at: "2026-10-28T13:00:00Z",
          action: "something_new",
          resource_type: "patient",
          resource_id: "client-2",
          patient_id: "client-2",
          client_name: null,
        },
      ],
    }
    render(<AwayPage />)
    fireEvent.click(screen.getByTestId("covered-period-show-period-1"))

    const shown = screen.getByTestId("coverage-activity-period-1")
    expect(shown).toHaveTextContent("Avery Client · Opened the record")
    expect(shown).toHaveTextContent("Other activity")
    expect(screen.getByRole("link", { name: "Avery Client" })).toHaveAttribute(
      "href",
      "/dashboard/patients/client-1",
    )
  })

  it("answers a colleague's request for its own period", () => {
    requests.current = {
      requests: [
        {
          period_id: "asked-1",
          clinician_id: "robin",
          clinician_name: "Robin Avery",
          start_date: "2026-10-26",
          end_date: "2026-10-30",
          return_date: "2026-11-02",
          status: "requested",
          active: false,
        },
        {
          period_id: "asked-2",
          clinician_id: "jo",
          clinician_name: "Jo Lindqvist",
          start_date: "2026-11-09",
          end_date: "2026-11-13",
          return_date: "2026-11-16",
          status: "accepted",
          active: false,
        },
      ],
    }
    render(<AwayPage />)

    fireEvent.click(screen.getByTestId("coverage-accept-asked-1"))
    expect(answer).toHaveBeenLastCalledWith({ periodId: "asked-1", answer: "accept" })

    expect(screen.queryByTestId("coverage-accept-asked-2")).not.toBeInTheDocument()
    fireEvent.click(screen.getByTestId("coverage-decline-asked-2"))
    expect(answer).toHaveBeenLastCalledWith({ periodId: "asked-2", answer: "decline" })
    expect(screen.getByTestId("coverage-decline-asked-2")).toHaveTextContent("Stop covering")
  })

  it("shows no coverage card when nobody has asked", () => {
    requests.current = { requests: [] }
    render(<AwayPage />)

    expect(screen.queryByTestId("coverage-requests")).not.toBeInTheDocument()
  })
})
