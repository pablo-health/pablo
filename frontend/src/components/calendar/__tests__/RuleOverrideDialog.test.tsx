// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { RuleOverrideDialog, summarizeConflicts } from "../RuleOverrideDialog"
import type { ConflictResponse, EnforcementLevel, RuleType } from "@/types/availability"

function conflict(
  rule_type: RuleType,
  enforcement: EnforcementLevel = "hard",
  message = `${rule_type} conflict`,
): ConflictResponse {
  return { rule_type, enforcement, message }
}

const FIVE_CONFLICTS: ConflictResponse[] = [
  conflict("working_hours", "soft"),
  conflict("block_day_of_week"),
  conflict("buffer_after"),
  conflict("block_time_range", "soft"),
  conflict("max_per_day"),
]

describe("summarizeConflicts", () => {
  it("states one rule as one sentence, in the therapist's own words", () => {
    expect(summarizeConflicts([conflict("block_day_of_week")])).toBe(
      "You've blocked that day of the week.",
    )
  })

  it("frames a soft rule as a habit rather than a boundary", () => {
    expect(summarizeConflicts([conflict("block_day_of_week", "soft")])).toBe(
      "You usually don't book that day of the week.",
    )
  })

  it("joins two rules into one sentence, hard before soft", () => {
    expect(
      summarizeConflicts([conflict("max_per_day", "soft"), conflict("block_day_of_week")]),
    ).toBe(
      "You've blocked that day of the week and it's more than you usually book in a day.",
    )
  })

  it("leads with three and counts the rest when a window trips five rules", () => {
    expect(summarizeConflicts(FIVE_CONFLICTS)).toBe(
      "You've blocked that day of the week, it's past your limit for one day, and it " +
        "doesn't leave the gap you keep after an appointment — and 2 more reasons.",
    )
  })

  it("counts a single leftover in the singular", () => {
    expect(summarizeConflicts(FIVE_CONFLICTS.slice(0, 4))).toContain("and 1 more reason.")
  })

  it("reads two rules of the same kind as one reason", () => {
    const summary = summarizeConflicts([
      conflict("block_specific_dates"),
      conflict("block_specific_dates"),
    ])
    expect(summary).toBe("You've blocked that date.")
  })

  it("says nothing when there is nothing to say", () => {
    expect(summarizeConflicts([])).toBe("")
  })
})

describe("RuleOverrideDialog", () => {
  it("states the reason and asks about this one event", () => {
    render(
      <RuleOverrideDialog
        open
        conflicts={[conflict("block_day_of_week")]}
        onOverride={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText("You've blocked that day of the week.")).toBeInTheDocument()
    expect(screen.getByText("Do you want to override this event?")).toBeInTheDocument()
  })

  it("carries a Pablo image", () => {
    render(
      <RuleOverrideDialog
        open
        conflicts={[conflict("block_day_of_week")]}
        onOverride={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByAltText("Pablo bear")).toBeInTheDocument()
  })

  it("keeps the per-rule detail collapsed rather than dumping it in the sentence", () => {
    render(
      <RuleOverrideDialog open conflicts={FIVE_CONFLICTS} onOverride={vi.fn()} onCancel={vi.fn()} />,
    )

    expect(screen.getByText("All the rules in full")).toBeInTheDocument()
    expect(screen.getByText(/block_day_of_week conflict/)).toBeInTheDocument()
  })

  it("says the series conflicts without naming occurrences", () => {
    render(
      <RuleOverrideDialog
        open
        recurring
        conflicts={[conflict("block_day_of_week")]}
        onOverride={vi.fn()}
        onCancel={vi.fn()}
      />,
    )

    expect(screen.getByText(/This series runs into your availability rules\./)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Override this series" })).toBeInTheDocument()
  })

  it("fires the override", async () => {
    const user = userEvent.setup()
    const onOverride = vi.fn()
    render(
      <RuleOverrideDialog
        open
        conflicts={[conflict("block_day_of_week")]}
        onOverride={onOverride}
        onCancel={vi.fn()}
      />,
    )

    await user.click(screen.getByRole("button", { name: "Override this event" }))

    expect(onOverride).toHaveBeenCalledTimes(1)
  })

  it("fires the cancel", async () => {
    const user = userEvent.setup()
    const onCancel = vi.fn()
    render(
      <RuleOverrideDialog
        open
        conflicts={[conflict("block_day_of_week")]}
        onOverride={vi.fn()}
        onCancel={onCancel}
      />,
    )

    await user.click(screen.getByRole("button", { name: "Cancel" }))

    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it("offers no way to change, delete or disable a rule", () => {
    render(
      <RuleOverrideDialog open conflicts={FIVE_CONFLICTS} onOverride={vi.fn()} onCancel={vi.fn()} />,
    )

    // Override and Cancel, plus the dialog's own close affordance — and
    // nothing that would edit the rules themselves.
    for (const label of [/unblock/i, /delete/i, /disable/i, /edit rule/i, /settings/i]) {
      expect(screen.queryByRole("button", { name: label })).not.toBeInTheDocument()
      expect(screen.queryByRole("link", { name: label })).not.toBeInTheDocument()
    }
  })

  it("reaches no rule mutation at all", () => {
    const source = readFileSync(
      resolve(process.cwd(), "src/components/calendar/RuleOverrideDialog.tsx"),
      "utf8",
    )

    for (const mutation of [
      "useDeleteAvailabilityRule",
      "useUpdateAvailabilityRule",
      "useCreateAvailabilityRule",
      "deleteAvailabilityRule",
      "updateAvailabilityRule",
    ]) {
      expect(source).not.toContain(mutation)
    }
  })
})
