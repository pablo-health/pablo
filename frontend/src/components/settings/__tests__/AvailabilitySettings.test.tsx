// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import {
  AvailabilitySettings,
  schedulingDefaultsFromRules,
  schedulingDefaultsToRulePayloads,
} from "../AvailabilitySettings"
import type { AvailabilityRule } from "@/types/availability"

const mutateCreate = vi.fn()
const mutateUpdate = vi.fn()
const mutateDelete = vi.fn()
const mutateParse = vi.fn()

let rulesData: AvailabilityRule[] = []
let listLoading = false
let listErrored = false
let preferencesData: { working_hours_start: number; working_hours_end: number } | undefined = {
  working_hours_start: 8,
  working_hours_end: 18,
}

vi.mock("@/hooks/useAvailability", () => ({
  useAvailabilityRules: () => ({
    data: { data: rulesData, total: rulesData.length },
    isLoading: listLoading,
    error: listErrored ? new Error("boom") : null,
  }),
  useCreateAvailabilityRule: () => ({ mutate: mutateCreate, isPending: false }),
  useUpdateAvailabilityRule: () => ({ mutate: mutateUpdate, isPending: false }),
  useDeleteAvailabilityRule: () => ({ mutate: mutateDelete, isPending: false }),
  useParseAvailabilityRules: () => ({ mutate: mutateParse, isPending: false }),
}))

vi.mock("@/hooks/usePreferences", () => ({
  usePreferences: () => ({ data: preferencesData }),
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

function renderWithClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <AvailabilitySettings />
    </QueryClientProvider>
  )
}

describe("AvailabilitySettings", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    rulesData = []
    listLoading = false
    listErrored = false
    preferencesData = { working_hours_start: 8, working_hours_end: 18 }
  })

  it("mounts the natural-language rule entry beside the rules list", () => {
    renderWithClient()

    expect(screen.getByLabelText(/describe your availability/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Parse" })).toBeInTheDocument()
  })

  it("shows an explanatory empty state when there are no rules", () => {
    renderWithClient()

    expect(
      screen.getByText(/don't have any availability rules yet/i)
    ).toBeInTheDocument()
  })

  describe("seed from calendar display hours", () => {
    it("shows the seed action when there are no rules, and does not create anything on its own", () => {
      renderWithClient()

      expect(
        screen.getByRole("button", { name: "Start from your calendar display hours" })
      ).toBeInTheDocument()
      expect(mutateCreate).not.toHaveBeenCalled()
    })

    it("hides the seed action once a rule exists", () => {
      rulesData = [makeRule({ id: "r1" })]
      renderWithClient()

      expect(
        screen.queryByRole("button", { name: "Start from your calendar display hours" })
      ).not.toBeInTheDocument()
    })

    it("creates five weekday working_hours rules from the display preference", async () => {
      const user = userEvent.setup()
      renderWithClient()

      await user.click(
        screen.getByRole("button", { name: "Start from your calendar display hours" })
      )

      expect(mutateCreate).toHaveBeenCalledTimes(5)
      for (let dayOfWeek = 0; dayOfWeek <= 4; dayOfWeek++) {
        expect(mutateCreate).toHaveBeenCalledWith(
          {
            rule_type: "working_hours",
            enforcement: "hard",
            params: { day_of_week: dayOfWeek, start: "08:00", end: "18:00" },
          },
          expect.anything()
        )
      }
    })
  })

  it("lists existing rules grouped by category", () => {
    rulesData = [
      makeRule({ id: "r1", rule_type: "block_day_of_week", params: { day_of_week: 4 } }),
      makeRule({
        id: "r2",
        rule_type: "max_per_day",
        enforcement: "soft",
        params: { max: 6 },
      }),
    ]

    renderWithClient()

    expect(screen.getByText("Blocked time")).toBeInTheDocument()
    expect(screen.getByText("Limits & buffers")).toBeInTheDocument()
    expect(screen.getByText("Friday blocked")).toBeInTheDocument()
    expect(screen.getByText("Max 6 appointments per day")).toBeInTheDocument()
    expect(screen.getByText("Soft — allows override")).toBeInTheDocument()
  })

  it("offers all eight rule types in the rule-type picker", async () => {
    const user = userEvent.setup()
    renderWithClient()

    await user.click(screen.getByRole("button", { name: "Add rule" }))
    await user.click(screen.getByRole("combobox", { name: /rule type/i }))

    const listbox = screen.getByRole("listbox")
    const options = within(listbox).getAllByRole("option")
    expect(options).toHaveLength(8)

    const expectedLabels = [
      "Working hours",
      "Block a day of the week",
      "Block a time range",
      "Limit appointments per day",
      "Buffer before appointments",
      "Buffer after appointments",
      "Block a date range",
      "Block specific dates",
    ]
    for (const label of expectedLabels) {
      expect(within(listbox).getByText(label)).toBeInTheDocument()
    }
  })

  it("switches the params form fields when the rule type changes", async () => {
    const user = userEvent.setup()
    renderWithClient()

    await user.click(screen.getByRole("button", { name: "Add rule" }))

    // Default type is "Working hours" -> day + start/end fields.
    expect(screen.getByLabelText("Start")).toBeInTheDocument()
    expect(screen.getByLabelText("End")).toBeInTheDocument()

    await user.click(screen.getByRole("combobox", { name: /rule type/i }))
    await user.click(screen.getByRole("option", { name: "Limit appointments per day" }))

    expect(screen.getByLabelText("Max appointments per day")).toBeInTheDocument()
    expect(screen.queryByLabelText("Start")).not.toBeInTheDocument()
  })

  it("rejects an end time before the start time", async () => {
    const user = userEvent.setup()
    renderWithClient()

    await user.click(screen.getByRole("button", { name: "Add rule" }))
    await user.click(screen.getByRole("combobox", { name: /rule type/i }))
    await user.click(screen.getByRole("option", { name: "Block a time range" }))

    const startInput = screen.getByLabelText("Start") as HTMLInputElement
    const endInput = screen.getByLabelText("End") as HTMLInputElement
    await user.clear(startInput)
    await user.type(startInput, "14:00")
    await user.clear(endInput)
    await user.type(endInput, "13:00")

    await user.click(screen.getByRole("button", { name: "Add rule" }))

    expect(
      await screen.findByText("End time must be after start time.")
    ).toBeInTheDocument()
    expect(mutateCreate).not.toHaveBeenCalled()
  })

  it("confirms before deleting a rule", async () => {
    rulesData = [makeRule({ id: "r1" })]
    const user = userEvent.setup()
    window.confirm = vi.fn().mockReturnValue(false)
    const confirmSpy = vi.mocked(window.confirm)

    renderWithClient()

    await user.click(screen.getByRole("button", { name: "Delete" }))
    expect(confirmSpy).toHaveBeenCalled()
    expect(mutateDelete).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    await user.click(screen.getByRole("button", { name: "Delete" }))
    expect(mutateDelete).toHaveBeenCalledWith("r1", expect.anything())
  })

  it("does not list session_defaults in the generic grouped rule list", () => {
    rulesData = [
      makeRule({
        id: "r1",
        rule_type: "session_defaults",
        enforcement: "soft",
        params: { duration_minutes: 50, alignment: "hour" },
      }),
    ]

    renderWithClient()

    expect(screen.queryByText("Scheduling defaults")).toBeInTheDocument() // section heading
    // The generic grouped list (with its own Edit/Delete buttons) never
    // renders a card for it — the dedicated fields section owns it.
    expect(screen.queryByText("Working hours")).not.toBeInTheDocument()
    expect(screen.queryByText("Blocked time")).not.toBeInTheDocument()
    expect(screen.queryByText("Limits & buffers")).not.toBeInTheDocument()
  })

  describe("scheduling defaults fields section", () => {
    it("shows length, break, and alignment from a session_defaults + buffer_after rule", () => {
      rulesData = [
        makeRule({
          id: "r1",
          rule_type: "session_defaults",
          enforcement: "soft",
          params: { duration_minutes: 50, alignment: "hour" },
        }),
        makeRule({
          id: "r2",
          rule_type: "buffer_after",
          enforcement: "hard",
          params: { minutes: 10 },
        }),
      ]

      renderWithClient()

      expect(screen.getByLabelText("Session length (minutes)")).toHaveValue(50)
      expect(screen.getByLabelText("Break between sessions (minutes)")).toHaveValue(10)
      expect(screen.getByRole("combobox", { name: /start-time alignment/i })).toHaveTextContent(
        "On the hour"
      )
    })

    it("shows the absent-rule default state", () => {
      rulesData = []
      renderWithClient()

      expect(screen.getByLabelText("Session length (minutes)")).toHaveValue(null)
      expect(screen.getByLabelText("Break between sessions (minutes)")).toHaveValue(0)
      expect(screen.getByRole("combobox", { name: /start-time alignment/i })).toHaveTextContent(
        "No alignment"
      )
    })

    it("saving issues a create-or-update for session_defaults and buffer_after", async () => {
      rulesData = []
      const user = userEvent.setup()
      renderWithClient()

      await user.type(screen.getByLabelText("Session length (minutes)"), "45")
      await user.clear(screen.getByLabelText("Break between sessions (minutes)"))
      await user.type(screen.getByLabelText("Break between sessions (minutes)"), "5")
      await user.click(screen.getByRole("combobox", { name: /start-time alignment/i }))
      await user.click(screen.getByRole("option", { name: "On the half hour" }))

      await user.click(screen.getByRole("button", { name: "Save scheduling defaults" }))

      expect(mutateCreate).toHaveBeenCalledWith(
        {
          rule_type: "session_defaults",
          enforcement: "soft",
          params: { duration_minutes: 45, alignment: "half_hour" },
        },
        expect.anything()
      )
      expect(mutateCreate).toHaveBeenCalledWith(
        { rule_type: "buffer_after", enforcement: "hard", params: { minutes: 5 } },
        expect.anything()
      )
    })

    it("updates existing rules rather than creating new ones", async () => {
      rulesData = [
        makeRule({
          id: "r1",
          rule_type: "session_defaults",
          enforcement: "soft",
          params: { duration_minutes: 50, alignment: "hour" },
        }),
        makeRule({
          id: "r2",
          rule_type: "buffer_after",
          enforcement: "hard",
          params: { minutes: 10 },
        }),
      ]
      const user = userEvent.setup()
      renderWithClient()

      await user.click(screen.getByRole("button", { name: "Save scheduling defaults" }))

      expect(mutateUpdate).toHaveBeenCalledWith(
        {
          ruleId: "r1",
          data: { params: { duration_minutes: 50, alignment: "hour" } },
        },
        expect.anything()
      )
      expect(mutateUpdate).toHaveBeenCalledWith(
        { ruleId: "r2", data: { params: { minutes: 10 } } },
        expect.anything()
      )
      expect(mutateCreate).not.toHaveBeenCalled()
    })

    it("deletes the buffer_after rule when break is set to 0", async () => {
      rulesData = [
        makeRule({
          id: "r2",
          rule_type: "buffer_after",
          enforcement: "hard",
          params: { minutes: 10 },
        }),
      ]
      const user = userEvent.setup()
      renderWithClient()

      await user.clear(screen.getByLabelText("Break between sessions (minutes)"))
      await user.type(screen.getByLabelText("Break between sessions (minutes)"), "0")
      await user.click(screen.getByRole("button", { name: "Save scheduling defaults" }))

      expect(mutateDelete).toHaveBeenCalledWith("r2", expect.anything())
    })
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
