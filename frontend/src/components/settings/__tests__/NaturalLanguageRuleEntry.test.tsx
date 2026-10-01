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

const mutateCreateType = vi.fn()
let appointmentTypes: { id: string; name: string }[] = []
vi.mock("@/hooks/useAppointmentTypes", () => ({
  useAppointmentTypes: () => ({ data: { data: appointmentTypes, total: appointmentTypes.length } }),
  useCreateAppointmentType: () => ({ mutate: mutateCreateType, isPending: false, isError: false }),
}))

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
      {
        rule_type: "block_day_of_week",
        enforcement: "hard",
        params: { day_of_week: 4 },
        appointment_type_id: null, allow_other_types: true,
      },
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
      {
        rule_type: "block_day_of_week",
        enforcement: "hard",
        params: { day_of_week: 4 },
        appointment_type_id: null, allow_other_types: true,
      },
      expect.anything()
    )
    // The second card's Create button is still there, untouched.
    expect(screen.getAllByRole("button", { name: "Create" })).toHaveLength(1)
  })

  it("renders a resolved date-range proposal and creates it only on explicit Create", async () => {
    parseResponse = {
      proposals: [
        {
          rule_type: "block_specific_dates",
          enforcement: "hard",
          params: { dates: ["2026-09-04"] },
          human_summary: "Blocked next Friday.",
        },
      ],
      could_not_parse: null,
      exclusive: false,
      existing_conflicting_rules: [],
    }
    mutateCreate.mockImplementation((_vars, opts) => opts.onSuccess({}))

    const user = await submitText("Block next Friday")

    expect(screen.getByText("2026-09-04 blocked")).toBeInTheDocument()
    expect(mutateCreate).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(mutateCreate).toHaveBeenCalledTimes(1)
    expect(mutateCreate).toHaveBeenCalledWith(
      {
        rule_type: "block_specific_dates",
        enforcement: "hard",
        params: { dates: ["2026-09-04"] },
        appointment_type_id: null, allow_other_types: true,
      },
      expect.anything()
    )
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
      {
        rule_type: "block_day_of_week",
        enforcement: "hard",
        params: { day_of_week: 5 },
        appointment_type_id: null, allow_other_types: true,
      },
      expect.anything()
    )
  })
})

describe("NaturalLanguageRuleEntry — appointment types", () => {
  const intakeCap = {
    rule_type: "max_per_week" as const,
    enforcement: "hard" as const,
    params: { max: 2 },
    human_summary: "At most 2 intakes a week.",
    appointment_type_id: "type-intake",
    allow_other_types: true,
  }

  beforeEach(() => {
    vi.clearAllMocks()
    appointmentTypes = [{ id: "type-intake", name: "Intake" }]
    mutateParse.mockImplementation((_vars, opts) => {
      opts.onSuccess(parseResponse)
    })
  })

  it("says which type a scoped proposal governs, and saves it with that scope", async () => {
    parseResponse = {
      proposals: [intakeCap],
      could_not_parse: null,
      exclusive: false,
      existing_conflicting_rules: [],
    }
    const user = await submitText("only two intakes a week")

    expect(screen.getByTestId("proposal-type-scope")).toHaveTextContent("Intake only")
    await user.click(screen.getByRole("button", { name: "Create" }))

    expect(mutateCreate.mock.calls[0][0]).toEqual({
      rule_type: "max_per_week",
      enforcement: "hard",
      params: { max: 2 },
      appointment_type_id: "type-intake",
      allow_other_types: true,
    })
  })

  it("offers both meanings of an ambiguous sentence; only the picked one becomes proposals", async () => {
    parseResponse = {
      proposals: [],
      could_not_parse: "A weekly cap, or a cap plus Tuesday hours?",
      refusal_reason: "ambiguous",
      exclusive: false,
      existing_conflicting_rules: [],
      readings: [
        { label: "Just a weekly cap", proposals: [intakeCap] },
        {
          label: "A cap, and intakes on Tuesdays",
          proposals: [
            intakeCap,
            {
              rule_type: "working_hours",
              enforcement: "hard",
              params: { day_of_week: 1, start: "09:00", end: "17:00" },
              human_summary: "Intakes on Tuesdays.",
              appointment_type_id: "type-intake",
            },
          ],
        },
      ],
    }
    const user = await submitText("two intakes a week on Tuesdays")

    expect(screen.getByText("A weekly cap, or a cap plus Tuesday hours?")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Create" })).toBeNull()

    await user.click(screen.getByRole("button", { name: /A cap, and intakes on Tuesdays/ }))

    expect(screen.getAllByRole("button", { name: "Create" })).toHaveLength(2)
    expect(mutateCreate).not.toHaveBeenCalled()
  })

  it("offers to add a missing type and reads the sentence again once it exists", async () => {
    parseResponse = {
      proposals: [],
      could_not_parse: 'This practice has no appointment type called "Group".',
      refusal_reason: "unknown_appointment_type",
      unknown_appointment_type: "Group",
      exclusive: false,
      existing_conflicting_rules: [],
    }
    mutateCreateType.mockImplementation((_data, opts) => opts.onSuccess())
    const user = await submitText("only two groups a week")

    const offer = screen.getByTestId("missing-appointment-type-offer")
    expect(within(offer).getByText(/You don.t have a .Group. appointment type yet/)).toBeInTheDocument()
    await user.click(within(offer).getByRole("button", { name: "Add Group" }))

    expect(mutateCreateType.mock.calls[0][0]).toEqual({
      name: "Group",
      duration_minutes: 50,
      audience: "existing",
    })
    expect(mutateParse).toHaveBeenCalledTimes(2)
  })

  it("suggests an hour and new clients for an intake-like type", async () => {
    parseResponse = {
      proposals: [],
      could_not_parse: "No such type.",
      refusal_reason: "unknown_appointment_type",
      unknown_appointment_type: "Evaluation",
      exclusive: false,
      existing_conflicting_rules: [],
    }
    const user = await submitText("two evaluations a week")
    await user.click(screen.getByRole("button", { name: "Add Evaluation" }))

    expect(mutateCreateType.mock.calls[0][0]).toEqual({
      name: "Evaluation",
      duration_minutes: 60,
      audience: "new",
    })
  })
})
