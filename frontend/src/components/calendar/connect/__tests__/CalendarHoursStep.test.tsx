// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ProposedAvailabilityRule } from "@/types/availability"
import { CalendarHoursStep } from "../CalendarHoursStep"
import { echoLines } from "../hoursCapture"

const parseRules = vi.hoisted(() => vi.fn())
const createRule = vi.hoisted(() => vi.fn())
vi.mock("@/hooks/useAvailability", () => ({
  useParseAvailabilityRules: () => ({
    mutateAsync: parseRules,
    isPending: false,
  }),
  useCreateAvailabilityRule: () => ({ mutateAsync: createRule }),
}))

const savePreferences = vi.hoisted(() => vi.fn())
const preferencesState = vi.hoisted(() => ({
  data: { timezone: "America/New_York" } as Record<string, unknown>,
}))
vi.mock("@/hooks/usePreferences", () => ({
  usePreferences: () => ({ data: preferencesState.data }),
  useSavePreferences: () => ({ mutateAsync: savePreferences }),
  detectBrowserTimezone: () => "America/New_York",
}))

function workingHours(day: number): ProposedAvailabilityRule {
  return {
    rule_type: "working_hours",
    enforcement: "hard",
    params: { day_of_week: day, start: "09:00", end: "17:00" },
    human_summary: "Working hours",
  }
}

const MON_TO_THU = [workingHours(0), workingHours(1), workingHours(2), workingHours(3)]

const onSaved = vi.fn()
const onSkip = vi.fn()

function renderStep() {
  return render(<CalendarHoursStep onSaved={onSaved} onSkip={onSkip} />)
}

async function describe_(user: ReturnType<typeof userEvent.setup>, sentence: string) {
  await user.type(screen.getByLabelText("Tell Pablo in your own words"), sentence)
  await user.click(screen.getByRole("button", { name: "Check this" }))
}

describe("CalendarHoursStep", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    preferencesState.data = { timezone: "America/New_York" }
    createRule.mockResolvedValue({})
    savePreferences.mockResolvedValue({})
  })

  it("offers example sentences that fill the box, parsed the same way as typing", async () => {
    const user = userEvent.setup()
    parseRules.mockResolvedValue({ proposals: MON_TO_THU, could_not_parse: null })
    renderStep()

    await user.click(screen.getByRole("button", { name: "No appointments before 10am" }))

    expect(screen.getByLabelText("Tell Pablo in your own words")).toHaveValue(
      "No appointments before 10am"
    )

    await user.click(screen.getByRole("button", { name: "Check this" }))

    expect(parseRules).toHaveBeenCalledWith({ text: "No appointments before 10am" })
  })

  it("writes nothing until the echo is confirmed", async () => {
    const user = userEvent.setup()
    parseRules.mockResolvedValue({ proposals: MON_TO_THU, could_not_parse: null })
    renderStep()

    await describe_(user, "I see clients Monday to Thursday, 9 to 5")

    expect(await screen.findByText("Monday to Thursday, 09:00 to 17:00")).toBeInTheDocument()
    expect(createRule).not.toHaveBeenCalled()

    await user.click(screen.getByRole("button", { name: "Yes, save this" }))

    await waitFor(() => expect(createRule).toHaveBeenCalledTimes(4))
    expect(createRule.mock.calls[0][0]).toEqual({
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week: 0, start: "09:00", end: "17:00" },
    })
    expect(onSaved).toHaveBeenCalled()
  })

  it("drops a line of the echo the practice does not recognise", async () => {
    const user = userEvent.setup()
    parseRules.mockResolvedValue({
      proposals: [
        ...MON_TO_THU,
        {
          rule_type: "block_day_of_week",
          enforcement: "hard",
          params: { day_of_week: 4 },
          human_summary: "No clients on Friday",
        },
      ],
      could_not_parse: null,
    })
    renderStep()

    await describe_(user, "Monday to Thursday, 9 to 5, Fridays are admin")
    await screen.findByText("No clients on Friday")

    const removeFriday = screen.getAllByRole("button", { name: "Remove" })[1]
    await user.click(removeFriday)
    await user.click(screen.getByRole("button", { name: "Yes, save this" }))

    await waitFor(() => expect(createRule).toHaveBeenCalledTimes(4))
    expect(
      createRule.mock.calls.some((call) => call[0].rule_type === "block_day_of_week")
    ).toBe(false)
  })

  it("asks again when the parser is unsure rather than guessing", async () => {
    const user = userEvent.setup()
    parseRules.mockResolvedValue({
      proposals: [],
      could_not_parse: "Afternoons when? Give me a time to work from.",
    })
    renderStep()

    await describe_(user, "afternoons I guess")

    expect(
      await screen.findByText("Afternoons when? Give me a time to work from.")
    ).toBeInTheDocument()
    expect(createRule).not.toHaveBeenCalled()
    expect(screen.queryByLabelText("Monday")).not.toBeInTheDocument()
  })

  it("falls back to the grid once the parser is unsure twice running", async () => {
    const user = userEvent.setup()
    parseRules.mockResolvedValue({ proposals: [], could_not_parse: "Not sure what you mean." })
    renderStep()

    await describe_(user, "afternoons I guess")
    await screen.findByText("Not sure what you mean.")
    await user.click(screen.getByRole("button", { name: "Check this" }))

    expect(await screen.findByLabelText("Monday")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Save these hours" })).toBeInTheDocument()
  })

  it("falls back to the grid when the parser cannot be reached at all", async () => {
    const user = userEvent.setup()
    parseRules.mockRejectedValue(new Error("503"))
    renderStep()

    await describe_(user, "Mondays and Tuesdays, 9 to 5")

    expect(await screen.findByLabelText("Monday")).toBeInTheDocument()
    // Nothing to go back to while the parser is down.
    expect(screen.queryByRole("button", { name: "Describe them instead" })).not.toBeInTheDocument()
  })

  it("keeps the grid reachable on purpose, and saves the same rules from it", async () => {
    const user = userEvent.setup()
    renderStep()

    await user.click(screen.getByRole("button", { name: "Pick from a grid instead" }))
    await user.click(screen.getByLabelText("Tuesday"))
    await user.click(screen.getByRole("button", { name: "Save these hours" }))

    await waitFor(() => expect(createRule).toHaveBeenCalledTimes(4))
    expect(createRule.mock.calls.map((call) => call[0].params.day_of_week)).toEqual([0, 2, 3, 4])
    expect(parseRules).not.toHaveBeenCalled()
  })

  it("pre-fills the detected timezone and saves a correction with the hours", async () => {
    const user = userEvent.setup()
    renderStep()

    await user.click(screen.getByRole("button", { name: "Pick from a grid instead" }))
    expect(screen.getByRole("combobox", { name: "Times are in" })).toHaveTextContent(
      "America/New York"
    )

    await user.click(screen.getByRole("combobox", { name: "Times are in" }))
    await user.click(screen.getByRole("option", { name: "America/Chicago" }))
    await user.click(screen.getByRole("button", { name: "Save these hours" }))

    await waitFor(() =>
      expect(savePreferences).toHaveBeenCalledWith({ timezone: "America/Chicago" })
    )
  })

  it("leaves the timezone alone when it was already right", async () => {
    const user = userEvent.setup()
    renderStep()

    await user.click(screen.getByRole("button", { name: "Pick from a grid instead" }))
    await user.click(screen.getByRole("button", { name: "Save these hours" }))

    await waitFor(() => expect(createRule).toHaveBeenCalled())
    expect(savePreferences).not.toHaveBeenCalled()
  })

  it("lets the practice skip, having said what will not work", async () => {
    const user = userEvent.setup()
    renderStep()

    expect(
      screen.getByText(
        "Until Pablo knows your hours it cannot offer times to a client, send session reminders, or let anyone book themselves."
      )
    ).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Skip for now" }))

    expect(onSkip).toHaveBeenCalled()
    expect(createRule).not.toHaveBeenCalled()
    expect(savePreferences).not.toHaveBeenCalled()
  })
})

describe("echoLines", () => {
  it("collapses consecutive days that share a range into one sentence", () => {
    expect(echoLines(MON_TO_THU)).toEqual([
      { text: "Monday to Thursday, 09:00 to 17:00", indexes: [0, 1, 2, 3] },
    ])
  })

  it("keeps a day with different hours on its own line", () => {
    const friday: ProposedAvailabilityRule = {
      rule_type: "working_hours",
      enforcement: "hard",
      params: { day_of_week: 4, start: "09:00", end: "12:00" },
      human_summary: "Friday mornings",
    }

    expect(echoLines([...MON_TO_THU, friday])).toEqual([
      { text: "Monday to Thursday, 09:00 to 17:00", indexes: [0, 1, 2, 3] },
      { text: "Friday, 09:00 to 12:00", indexes: [4] },
    ])
  })

  it("shows every other kind of rule in the parser's own words", () => {
    const cap: ProposedAvailabilityRule = {
      rule_type: "max_per_day",
      enforcement: "hard",
      params: { max: 2 },
      human_summary: "At most 2 appointments a day",
    }

    expect(echoLines([cap])).toEqual([{ text: "At most 2 appointments a day", indexes: [0] }])
  })
})
