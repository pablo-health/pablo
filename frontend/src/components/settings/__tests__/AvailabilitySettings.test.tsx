// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  BlockedTimeCard,
  LimitsAndBuffersCard,
  schedulingDefaultsFromRules,
  schedulingDefaultsToRulePayloads,
} from "../AvailabilitySettings"
import type { AvailabilityRule } from "@/types/availability"

const mutateCreate = vi.fn()
const mutateUpdate = vi.fn()
const mutateDelete = vi.fn()

let rulesData: AvailabilityRule[] = []
let listLoading = false
let listErrored = false

vi.mock("@/hooks/useAvailability", () => ({
  useAvailabilityRules: () => ({
    data: { data: rulesData, total: rulesData.length },
    isLoading: listLoading,
    error: listErrored ? new Error("boom") : null,
  }),
  useCreateAvailabilityRule: () => ({ mutate: mutateCreate, isPending: false }),
  useUpdateAvailabilityRule: () => ({ mutate: mutateUpdate, isPending: false }),
  useDeleteAvailabilityRule: () => ({ mutate: mutateDelete, isPending: false }),
}))

function makeRule(overrides: Partial<AvailabilityRule> = {}): AvailabilityRule {
  return {
    id: "rule_1",
    user_id: "user_1",
    rule_type: "block_day_of_week",
    enforcement: "hard",
    params: { day_of_week: 4 },
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>)
}

describe("BlockedTimeCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    rulesData = []
    listLoading = false
    listErrored = false
  })

  it("shows an empty state when there is no blocked time", () => {
    renderWithClient(<BlockedTimeCard />)

    expect(screen.getByText("No blocked time yet.")).toBeInTheDocument()
  })

  it("does not list working_hours or session_defaults rules", () => {
    rulesData = [
      makeRule({ id: "r1", rule_type: "working_hours", params: { day_of_week: 0, start: "09:00", end: "17:00" } }),
      makeRule({ id: "r2", rule_type: "session_defaults", params: { duration_minutes: 50 } }),
    ]

    renderWithClient(<BlockedTimeCard />)

    expect(screen.getByText("No blocked time yet.")).toBeInTheDocument()
  })

  it("lists blocked-time rules with the plain-language enforcement label", () => {
    rulesData = [
      makeRule({ id: "r1", rule_type: "block_day_of_week", enforcement: "hard", params: { day_of_week: 4 } }),
      makeRule({ id: "r2", rule_type: "block_time_range", enforcement: "soft", params: { start: "12:00", end: "13:00" } }),
    ]

    renderWithClient(<BlockedTimeCard />)

    expect(screen.getByText(/Friday blocked/)).toBeInTheDocument()
    expect(screen.getByText(/Always enforced/)).toBeInTheDocument()
    expect(screen.getByText(/Warns, still bookable/)).toBeInTheDocument()
  })

  it("opens the rule form via the Block time button and creates a rule", async () => {
    const user = userEvent.setup()
    renderWithClient(<BlockedTimeCard />)

    await user.click(screen.getByRole("button", { name: "Block time" }))
    await user.click(screen.getByRole("combobox", { name: /rule type/i }))
    await user.click(screen.getByRole("option", { name: "Block a day of the week" }))
    await user.click(screen.getByRole("button", { name: "Add rule" }))

    expect(mutateCreate).toHaveBeenCalledWith(
      { rule_type: "block_day_of_week", enforcement: "hard", params: { day_of_week: 6 } },
      expect.anything()
    )
  })

  it("confirms before removing a rule", async () => {
    rulesData = [makeRule({ id: "r1" })]
    const user = userEvent.setup()
    window.confirm = vi.fn().mockReturnValue(false)
    const confirmSpy = vi.mocked(window.confirm)

    renderWithClient(<BlockedTimeCard />)

    await user.click(screen.getByRole("button", { name: "Remove" }))
    expect(confirmSpy).toHaveBeenCalled()
    expect(mutateDelete).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByRole("button", { name: "Remove" }))
    expect(mutateDelete).toHaveBeenCalledWith("r1", expect.anything())
  })

  it("offers all eight rule types in the rule-type picker", async () => {
    const user = userEvent.setup()
    renderWithClient(<BlockedTimeCard />)

    await user.click(screen.getByRole("button", { name: "Block time" }))
    await user.click(screen.getByRole("combobox", { name: /rule type/i }))

    const listbox = screen.getByRole("listbox")
    const options = within(listbox).getAllByRole("option")
    expect(options).toHaveLength(8)
  })

  it("rejects an end time before the start time", async () => {
    const user = userEvent.setup()
    renderWithClient(<BlockedTimeCard />)

    await user.click(screen.getByRole("button", { name: "Block time" }))
    await user.click(screen.getByRole("combobox", { name: /rule type/i }))
    await user.click(screen.getByRole("option", { name: "Block a time range" }))

    const startInput = screen.getByLabelText("Start") as HTMLInputElement
    const endInput = screen.getByLabelText("End") as HTMLInputElement
    await user.clear(startInput)
    await user.type(startInput, "14:00")
    await user.clear(endInput)
    await user.type(endInput, "13:00")

    await user.click(screen.getByRole("button", { name: "Add rule" }))

    expect(await screen.findByText("End time must be after start time.")).toBeInTheDocument()
    expect(mutateCreate).not.toHaveBeenCalled()
  })
})

describe("LimitsAndBuffersCard", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    rulesData = []
  })

  it("shows the absent-rule default state", () => {
    renderWithClient(<LimitsAndBuffersCard />)

    expect(screen.getByLabelText("Sessions per day")).toHaveValue(null)
    expect(screen.getByLabelText("Default session length")).toHaveValue(null)
    expect(screen.getByLabelText("Break after each session")).toHaveValue(0)
  })

  it("shows values from existing rules", () => {
    rulesData = [
      makeRule({ id: "r1", rule_type: "max_per_day", enforcement: "soft", params: { max: 6 } }),
      makeRule({
        id: "r2",
        rule_type: "session_defaults",
        enforcement: "soft",
        params: { duration_minutes: 50, alignment: "hour" },
      }),
      makeRule({ id: "r3", rule_type: "buffer_after", enforcement: "hard", params: { minutes: 10 } }),
    ]

    renderWithClient(<LimitsAndBuffersCard />)

    expect(screen.getByLabelText("Sessions per day")).toHaveValue(6)
    expect(screen.getByLabelText("Default session length")).toHaveValue(50)
    expect(screen.getByLabelText("Break after each session")).toHaveValue(10)
  })

  it("creates a max_per_day rule when sessions per day is set for the first time", async () => {
    const user = userEvent.setup()
    renderWithClient(<LimitsAndBuffersCard />)

    const input = screen.getByLabelText("Sessions per day")
    await user.type(input, "6")
    await user.tab()

    expect(mutateCreate).toHaveBeenCalledWith(
      { rule_type: "max_per_day", enforcement: "soft", params: { max: 6 } },
      expect.anything()
    )
  })

  it("updates the existing max_per_day rule rather than creating a new one", async () => {
    rulesData = [makeRule({ id: "r1", rule_type: "max_per_day", enforcement: "soft", params: { max: 6 } })]
    const user = userEvent.setup()
    renderWithClient(<LimitsAndBuffersCard />)

    const input = screen.getByLabelText("Sessions per day")
    await user.clear(input)
    await user.type(input, "8")
    await user.tab()

    expect(mutateUpdate).toHaveBeenCalledWith({ ruleId: "r1", data: { params: { max: 8 } } }, expect.anything())
    expect(mutateCreate).not.toHaveBeenCalled()
  })

  it("deletes the buffer_after rule when break is set to 0", async () => {
    rulesData = [makeRule({ id: "r1", rule_type: "buffer_after", enforcement: "hard", params: { minutes: 10 } })]
    const user = userEvent.setup()
    renderWithClient(<LimitsAndBuffersCard />)

    const input = screen.getByLabelText("Break after each session")
    await user.clear(input)
    await user.type(input, "0")
    await user.tab()

    expect(mutateDelete).toHaveBeenCalledWith("r1", expect.anything())
  })
})

describe("schedulingDefaultsFromRules", () => {
  it("maps a session_defaults + buffer_after rule to fields", () => {
    const rules: AvailabilityRule[] = [
      makeRule({
        id: "r1",
        rule_type: "session_defaults",
        params: { duration_minutes: 60, alignment: "half_hour" },
      }),
      makeRule({ id: "r2", rule_type: "buffer_after", params: { minutes: 15 } }),
    ]

    expect(schedulingDefaultsFromRules(rules)).toEqual({
      durationMinutes: "60",
      breakMinutes: "15",
      alignment: "half_hour",
    })
  })

  it("defaults to empty/zero/none when no rules are present", () => {
    expect(schedulingDefaultsFromRules([])).toEqual({
      durationMinutes: "",
      breakMinutes: "0",
      alignment: "none",
    })
  })
})

describe("schedulingDefaultsToRulePayloads", () => {
  it("maps fields to a session_defaults params object and a break minute count", () => {
    expect(
      schedulingDefaultsToRulePayloads({
        durationMinutes: "45",
        breakMinutes: "5",
        alignment: "hour",
      })
    ).toEqual({
      sessionDefaultsParams: { duration_minutes: 45, alignment: "hour" },
      breakMinutes: 5,
    })
  })

  it("omits duration_minutes and alignment when unset", () => {
    expect(
      schedulingDefaultsToRulePayloads({ durationMinutes: "", breakMinutes: "0", alignment: "none" })
    ).toEqual({
      sessionDefaultsParams: {},
      breakMinutes: 0,
    })
  })
})
