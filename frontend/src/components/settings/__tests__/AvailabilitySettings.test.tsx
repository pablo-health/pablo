// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { AvailabilitySettings } from "../AvailabilitySettings"
import type { AvailabilityRule } from "@/types/availability"

const mutateCreate = vi.fn()
const mutateUpdate = vi.fn()
const mutateDelete = vi.fn()
const mutateParse = vi.fn()

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
  useParseAvailabilityRules: () => ({ mutate: mutateParse, isPending: false }),
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
})
