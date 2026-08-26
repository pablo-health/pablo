// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { NaturalLanguageRuleEntry } from "../NaturalLanguageRuleEntry"
import type { ParseAvailabilityRulesResponse } from "@/types/availability"

const mutateCreate = vi.fn()
const mutateParse = vi.fn()

let parseResponse: ParseAvailabilityRulesResponse = {
  proposals: [],
  could_not_parse: null,
  exclusive: false,
  existing_conflicting_rules: [],
}

vi.mock("@/hooks/useAvailability", () => ({
  useCreateAvailabilityRule: () => ({ mutate: mutateCreate, isPending: false }),
  useParseAvailabilityRules: () => ({ mutate: mutateParse, isPending: false }),
}))

function blockFridayProposal() {
  return {
    rule_type: "block_day_of_week" as const,
    enforcement: "hard" as const,
    params: { day_of_week: 4 },
    human_summary: "No appointments on Fridays.",
  }
}

async function submitText(text: string) {
  const user = userEvent.setup()
  render(<NaturalLanguageRuleEntry />)

  await user.type(screen.getByLabelText(/describe your availability/i), text)
  await user.click(screen.getByRole("button", { name: "Parse" }))
  return user
}

describe("NaturalLanguageRuleEntry", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    parseResponse = {
      proposals: [],
      could_not_parse: null,
      exclusive: false,
      existing_conflicting_rules: [],
    }
    mutateParse.mockImplementation((_vars, opts) => {
      opts.onSuccess(parseResponse)
    })
  })

  it("renders the structured preview using the same rendering as an existing rule row", async () => {
    parseResponse = {
      proposals: [blockFridayProposal()],
      could_not_parse: null,
      exclusive: false,
      existing_conflicting_rules: [],
    }

    await submitText("No appointments on Fridays")

    expect(screen.getByText("Block a day of the week")).toBeInTheDocument()
    expect(screen.getByText("Friday blocked")).toBeInTheDocument()
    expect(mutateCreate).not.toHaveBeenCalled()
  })

  it("fires the create hook exactly once with the proposal verbatim on Create", async () => {
    parseResponse = {
      proposals: [blockFridayProposal()],
      could_not_parse: null,
      exclusive: false,
      existing_conflicting_rules: [],
    }
    mutateCreate.mockImplementation((_vars, opts) => opts.onSuccess({}))

    const user = await submitText("No appointments on Fridays")
    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(mutateCreate).toHaveBeenCalledTimes(1)
    expect(mutateCreate).toHaveBeenCalledWith(
      { rule_type: "block_day_of_week", enforcement: "hard", params: { day_of_week: 4 } },
      expect.anything()
    )
  })

  it("renders two individually confirmable cards for a two-proposal parse", async () => {
    parseResponse = {
      proposals: [
        blockFridayProposal(),
        {
          rule_type: "max_per_day",
          enforcement: "hard",
          params: { max: 5 },
          human_summary: "At most five a day.",
        },
      ],
      could_not_parse: null,
      exclusive: false,
      existing_conflicting_rules: [],
    }
    mutateCreate.mockImplementation((_vars, opts) => opts.onSuccess({}))

    const user = await submitText("No Fridays, and at most 5 a day")

    const createButtons = screen.getAllByRole("button", { name: "Create" })
    expect(createButtons).toHaveLength(2)

    await user.click(createButtons[0])

    expect(mutateCreate).toHaveBeenCalledTimes(1)
    expect(mutateCreate).toHaveBeenCalledWith(
      { rule_type: "block_day_of_week", enforcement: "hard", params: { day_of_week: 4 } },
      expect.anything()
    )
    // The second card's Create button is still there, untouched.
    expect(screen.getAllByRole("button", { name: "Create" })).toHaveLength(1)
  })

  it("shows the could_not_parse reason with no Create button", async () => {
    parseResponse = {
      proposals: [],
      could_not_parse: "That mentions a specific date, which isn't supported here.",
      exclusive: false,
      existing_conflicting_rules: [],
    }

    await submitText("Block Dec 24th")

    expect(
      screen.getByText("That mentions a specific date, which isn't supported here.")
    ).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Create" })).not.toBeInTheDocument()
  })

  it("opens RuleForm populated with parsed values on Edit and submits edits through the create hook", async () => {
    parseResponse = {
      proposals: [blockFridayProposal()],
      could_not_parse: null,
      exclusive: false,
      existing_conflicting_rules: [],
    }
    mutateCreate.mockImplementation((_vars, opts) => opts.onSuccess({}))

    const user = await submitText("No appointments on Fridays")
    await user.click(screen.getByRole("button", { name: "Edit" }))

    const daySelect = screen.getByRole("combobox", { name: /day to block/i })
    expect(within(daySelect).getByText("Friday")).toBeInTheDocument()

    await user.click(daySelect)
    await user.click(screen.getByRole("option", { name: "Saturday" }))
    await user.click(screen.getByRole("button", { name: "Save changes" }))

    expect(mutateCreate).toHaveBeenCalledTimes(1)
    expect(mutateCreate).toHaveBeenCalledWith(
      { rule_type: "block_day_of_week", enforcement: "hard", params: { day_of_week: 5 } },
      expect.anything()
    )
  })
})
