// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { AvailabilitySettings } from "../AvailabilitySettings"
import { RULE_TYPE_OPTIONS } from "@/types/availability"
import type { AvailabilityRuleResponse } from "@/types/availability"

const createMutate = vi.hoisted(() => vi.fn())
const updateMutate = vi.hoisted(() => vi.fn())
const deleteMutate = vi.hoisted(() => vi.fn())
const rulesState = vi.hoisted(() => ({
  data: undefined as { data: AvailabilityRuleResponse[]; total: number } | undefined,
  isLoading: false,
  error: null as Error | null,
}))

vi.mock("@/hooks/useAvailabilityRules", () => ({
  useAvailabilityRules: () => rulesState,
  useCreateAvailabilityRule: () => ({
    mutateAsync: createMutate,
    isPending: false,
  }),
  useUpdateAvailabilityRule: () => ({
    mutateAsync: updateMutate,
    isPending: false,
  }),
  useDeleteAvailabilityRule: () => ({
    mutateAsync: deleteMutate,
    isPending: false,
    variables: undefined,
  }),
}))

function makeRule(overrides: Partial<AvailabilityRuleResponse> = {}): AvailabilityRuleResponse {
  return {
    id: "rule-1",
    user_id: "user-1",
    rule_type: "block_day_of_week",
    enforcement: "hard",
    params: { day_of_week: 5 },
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

beforeEach(() => {
  createMutate.mockReset().mockResolvedValue(makeRule())
  updateMutate.mockReset().mockResolvedValue(makeRule())
  deleteMutate.mockReset().mockResolvedValue(undefined)
  rulesState.data = { data: [], total: 0 }
  rulesState.isLoading = false
  rulesState.error = null
  vi.restoreAllMocks()
})

describe("AvailabilitySettings", () => {
  it("shows an explanatory empty state when there are no rules", () => {
    render(<AvailabilitySettings />)
    expect(screen.getByText(/No availability rules yet/i)).toBeInTheDocument()
    expect(screen.getByText(/limits when appointments can be booked/i)).toBeInTheDocument()
  })

  it("lists existing rules with type, enforcement, and a summary", () => {
    rulesState.data = {
      data: [
        makeRule({ id: "r1", rule_type: "block_day_of_week", params: { day_of_week: 5 } }),
        makeRule({
          id: "r2",
          rule_type: "max_per_day",
          enforcement: "soft",
          params: { max: 6 },
        }),
      ],
      total: 2,
    }
    render(<AvailabilitySettings />)

    expect(screen.getByText("Block a day of the week")).toBeInTheDocument()
    expect(screen.getByText("Saturday")).toBeInTheDocument()
    expect(screen.getByText("Max appointments per day")).toBeInTheDocument()
    expect(screen.getByText("6 appointments per day")).toBeInTheDocument()
    expect(screen.getByText("Hard")).toBeInTheDocument()
    expect(screen.getByText("Soft")).toBeInTheDocument()
  })

  it("offers all eight rule types in the create picker", async () => {
    const user = userEvent.setup()
    render(<AvailabilitySettings />)

    await user.click(screen.getByRole("button", { name: /add rule/i }))
    await user.click(screen.getByRole("combobox", { name: /rule type/i }))

    for (const option of RULE_TYPE_OPTIONS) {
      expect(screen.getByRole("option", { name: option.label })).toBeInTheDocument()
    }
    expect(screen.getAllByRole("option")).toHaveLength(8)
  })

  it("switches the params form fields when the rule type changes", async () => {
    const user = userEvent.setup()
    render(<AvailabilitySettings />)

    await user.click(screen.getByRole("button", { name: /add rule/i }))

    // Default type (working_hours) shows day-of-week + start/end time.
    expect(screen.getByLabelText("Start time")).toBeInTheDocument()
    expect(screen.getByLabelText("Day of week")).toBeInTheDocument()
    expect(screen.queryByLabelText(/max appointments per day/i)).not.toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: /rule type/i }))
    await user.click(screen.getByRole("option", { name: "Max appointments per day" }))

    expect(screen.getByLabelText(/max appointments per day/i)).toBeInTheDocument()
    expect(screen.queryByLabelText("Start time")).not.toBeInTheDocument()
    expect(screen.queryByLabelText("Day of week")).not.toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: /rule type/i }))
    await user.click(screen.getByRole("option", { name: "Block a date range" }))

    expect(screen.getByLabelText("Start date")).toBeInTheDocument()
    expect(screen.getByLabelText("End date")).toBeInTheDocument()
  })

  it("rejects an end time before the start time without calling the API", async () => {
    const user = userEvent.setup()
    render(<AvailabilitySettings />)

    await user.click(screen.getByRole("button", { name: /add rule/i }))
    await user.click(screen.getByRole("combobox", { name: /rule type/i }))
    await user.click(screen.getByRole("option", { name: "Block a time range" }))

    await user.type(screen.getByLabelText("Start time"), "14:00")
    await user.type(screen.getByLabelText("End time"), "13:00")
    await user.click(screen.getByRole("button", { name: /add rule/i }))

    await waitFor(() => {
      expect(screen.getByText(/end time must be after start time/i)).toBeInTheDocument()
    })
    expect(createMutate).not.toHaveBeenCalled()
  })

  it("explains hard vs soft enforcement in plain language", async () => {
    const user = userEvent.setup()
    render(<AvailabilitySettings />)

    await user.click(screen.getByRole("button", { name: /add rule/i }))

    expect(
      screen.getByText(
        /hard rules always block a conflicting booking; soft rules let it through but flag the conflict/i,
      ),
    ).toBeInTheDocument()
  })

  it("confirms before deleting a rule, and skips the delete if declined", async () => {
    const user = userEvent.setup()
    rulesState.data = { data: [makeRule({ id: "r1" })], total: 1 }
    window.confirm = vi.fn().mockReturnValue(false)

    render(<AvailabilitySettings />)
    await user.click(screen.getByRole("button", { name: /delete/i }))

    expect(window.confirm).toHaveBeenCalled()
    expect(deleteMutate).not.toHaveBeenCalled()
  })

  it("deletes the rule once the confirmation is accepted", async () => {
    const user = userEvent.setup()
    rulesState.data = { data: [makeRule({ id: "r1" })], total: 1 }
    window.confirm = vi.fn().mockReturnValue(true)

    render(<AvailabilitySettings />)
    await user.click(screen.getByRole("button", { name: /delete/i }))

    await waitFor(() => {
      expect(deleteMutate).toHaveBeenCalledWith({ ruleId: "r1" })
    })
  })
})
