// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The questions a practice writes itself, one renderer at a time.
 *
 * Two things are load-bearing in every block below, and they are the two a
 * patient notices when they are wrong.
 *
 * The **question** has to be the one the practice wrote. These renderers
 * have no wording of their own — `label` is the question and `help_text` the
 * line under it, and an item stored without a label has nothing to ask, so
 * it reads as a step still to come rather than as a heading somebody
 * invented.
 *
 * The **answer shape** has to be the one the save route stores. Each is
 * checked against what `backend/app/intake/answers.py` validates, because a
 * renderer that builds a plausible-looking value the route refuses fails as
 * a patient who cannot get past a question.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { IntakeAssignmentItem } from "@/lib/api/patientIntake"
import { rendererFor } from "../renderers/registry"
import type { AnswerValue } from "../renderers/types"
import { INTAKE_FORM, authoredItem } from "./formFixtures"

/**
 * What the walk hands a renderer that writes through a route of its own.
 *
 * Inert here: every renderer in this file collects a value and calls
 * `onChange`. The consent document is the exception and has its own file.
 */
const ROUTE_PROPS = {
  assignmentId: "00000000-0000-4000-8000-00000000000a",
  sessionToken: "session-token",
  artifacts: [],
  onWrote: () => {},
  onSessionLost: () => {},
}

function renderItem(item: IntakeAssignmentItem, value: AnswerValue | null = null) {
  const onChange = vi.fn()
  const renderer = rendererFor(item.item_type)
  render(
    <renderer.Component
      item={item}
      value={value}
      onChange={onChange}
      form={INTAKE_FORM}
      {...ROUTE_PROPS}
    />,
  )
  return { onChange, renderer }
}

const CHOICES = {
  options: [
    { key: "never", label: "Never" },
    { key: "sometimes", label: "Sometimes" },
    { key: "often", label: "Often" },
  ],
}

describe("the question above every authored control", () => {
  it("asks what the practice wrote, and shows its help text under it", () => {
    renderItem(
      authoredItem("free_text", {
        label: "How have you been sleeping?",
        helpText: "A sentence or two is plenty.",
      }),
    )

    expect(
      screen.getByRole("heading", { name: "How have you been sleeping?" }),
    ).toBeInTheDocument()
    expect(screen.getByTestId("forms-question-help")).toHaveTextContent(
      "A sentence or two is plenty.",
    )
  })

  it("shows no help text when the practice wrote none", () => {
    renderItem(authoredItem("free_text", { helpText: null }))

    expect(screen.queryByTestId("forms-question-help")).not.toBeInTheDocument()
  })

  it.each([
    "free_text",
    "single_choice",
    "multi_choice",
    "yes_no",
    "scale",
    "number",
    "date",
  ])("says %s is a step to come when nothing was written to ask", (itemType) => {
    renderItem(authoredItem(itemType, { label: null, config: CHOICES }))

    expect(screen.getByTestId("forms-item-unavailable")).toBeInTheDocument()
  })

  it.each([
    "free_text",
    "single_choice",
    "multi_choice",
    "yes_no",
    "scale",
    "number",
    "date",
  ])("calls the %s question by its own words on the review screen", (itemType) => {
    const item = authoredItem(itemType, { label: "Is this better?" })

    expect(rendererFor(itemType).label(item, INTAKE_FORM)).toBe("Is this better?")
    expect(rendererFor(itemType).answerable).toBe(true)
    expect(rendererFor(itemType).summary(null, item, INTAKE_FORM)).toBeNull()
  })
})

describe("a written answer", () => {
  it("stores the text under `text` and counts against the question's own cap", async () => {
    const user = userEvent.setup()
    const item = authoredItem("free_text", { config: { max_len: 50 } })
    const { onChange } = renderItem(item)

    expect(screen.getByTestId("forms-free-text-counter")).toHaveTextContent("0 / 50")
    await user.type(screen.getByTestId("forms-free-text"), "B")

    expect(onChange).toHaveBeenCalledWith({ text: "B" })
  })

  it("repeats what was written on the review screen", () => {
    const item = authoredItem("free_text")

    expect(rendererFor("free_text").summary({ text: "  Badly  " }, item, INTAKE_FORM)).toBe("Badly")
  })
})

describe("pick one", () => {
  it("stores the answer's key rather than its wording", async () => {
    const user = userEvent.setup()
    const item = authoredItem("single_choice", { config: CHOICES })
    const { onChange } = renderItem(item)

    const group = screen.getByTestId("forms-single-choice")
    await user.click(within(group).getByRole("radio", { name: "Sometimes" }))

    expect(onChange).toHaveBeenCalledWith({ key: "sometimes" })
  })

  it("reads the chosen answer back in words on the review screen", () => {
    const item = authoredItem("single_choice", { config: CHOICES })

    expect(rendererFor("single_choice").summary({ key: "often" }, item, INTAKE_FORM)).toBe("Often")
  })
})

describe("pick any", () => {
  it("adds and removes keys without disturbing the rest", async () => {
    const user = userEvent.setup()
    const item = authoredItem("multi_choice", { config: CHOICES })
    const { onChange } = renderItem(item, { keys: ["never"] })

    const group = screen.getByTestId("forms-multi-choice")
    await user.click(within(group).getByRole("checkbox", { name: "Often" }))

    expect(onChange).toHaveBeenCalledWith({ keys: ["never", "often"] })
  })

  it("unpicks an answer that was already picked", async () => {
    const user = userEvent.setup()
    const item = authoredItem("multi_choice", { config: CHOICES })
    const { onChange } = renderItem(item, { keys: ["never", "often"] })

    const group = screen.getByTestId("forms-multi-choice")
    await user.click(within(group).getByRole("checkbox", { name: "Never" }))

    expect(onChange).toHaveBeenCalledWith({ keys: ["often"] })
  })

  it("lists every chosen answer on the review screen", () => {
    const item = authoredItem("multi_choice", { config: CHOICES })
    const summary = rendererFor("multi_choice").summary(
      { keys: ["never", "often"] },
      item,
      INTAKE_FORM,
    )

    expect(summary).toBe("Never, Often")
  })
})

describe("yes or no", () => {
  it("stores a boolean under `yes`", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(authoredItem("yes_no"))

    await user.click(within(screen.getByTestId("forms-yes-no")).getByRole("radio", { name: "No" }))

    expect(onChange).toHaveBeenCalledWith({ yes: false })
  })

  it("opens the follow-up the practice attached, on a yes only", async () => {
    const user = userEvent.setup()
    const item = authoredItem("yes_no", { config: { follow_up_label: "What happened?" } })

    const { onChange } = renderItem(item, { yes: true })
    await user.type(screen.getByTestId("forms-yes-no-follow-up"), "A")

    expect(screen.getByText("What happened?")).toBeInTheDocument()
    expect(onChange).toHaveBeenCalledWith({ yes: true, follow_up: "A" })
  })

  it("keeps the follow-up box shut on a no", () => {
    const item = authoredItem("yes_no", { config: { follow_up_label: "What happened?" } })

    renderItem(item, { yes: false })

    expect(screen.queryByTestId("forms-yes-no-follow-up")).not.toBeInTheDocument()
  })

  it("carries the follow-up into the review row", () => {
    const item = authoredItem("yes_no", { config: { follow_up_label: "What happened?" } })
    const renderer = rendererFor("yes_no")

    expect(renderer.summary({ yes: true, follow_up: "A fall" }, item, INTAKE_FORM)).toBe(
      "Yes — A fall",
    )
    expect(renderer.summary({ yes: false }, item, INTAKE_FORM)).toBe("No")
  })
})

describe("a scale", () => {
  const scale = { min: 0, max: 4, min_label: "Not at all", max_label: "Constantly" }

  it("offers every point between its ends, with the words at each end", () => {
    renderItem(authoredItem("scale", { config: scale }))

    const group = screen.getByTestId("forms-scale")
    expect(within(group).getAllByRole("radio")).toHaveLength(5)
    expect(screen.getByTestId("forms-scale-min-label")).toHaveTextContent("Not at all")
    expect(screen.getByTestId("forms-scale-max-label")).toHaveTextContent("Constantly")
  })

  it("stores the point as a whole number under `value`", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(authoredItem("scale", { config: scale }))

    await user.click(within(screen.getByTestId("forms-scale")).getByRole("radio", { name: "3" }))

    expect(onChange).toHaveBeenCalledWith({ value: 3 })
  })

  it("reads the point back against the top of the scale, never as a band", () => {
    const item = authoredItem("scale", { config: scale })

    expect(rendererFor("scale").summary({ value: 3 }, item, INTAKE_FORM)).toBe("3 of 4")
  })
})

describe("a number", () => {
  it("stores a number under `value` and shows the unit beside the box", async () => {
    const user = userEvent.setup()
    const item = authoredItem("number", { config: { min: 0, max: 40, unit: "hours" } })
    const { onChange } = renderItem(item)

    expect(screen.getByTestId("forms-number-unit")).toHaveTextContent("hours")
    await user.type(screen.getByTestId("forms-number"), "7")

    expect(onChange).toHaveBeenCalledWith({ value: 7 })
  })

  it("treats an emptied box as no answer rather than as zero", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(authoredItem("number"), { value: 7 })

    await user.clear(screen.getByTestId("forms-number"))

    expect(onChange).toHaveBeenCalledWith({})
  })

  it("puts the unit on the review row", () => {
    const item = authoredItem("number", { config: { unit: "hours" } })

    expect(rendererFor("number").summary({ value: 7 }, item, INTAKE_FORM)).toBe("7 hours")
  })
})

describe("a date", () => {
  it("stores it the way the save route parses it", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(authoredItem("date"))

    await user.type(screen.getByTestId("forms-date"), "2026-03-14")

    expect(onChange).toHaveBeenLastCalledWith({ value: "2026-03-14" })
  })

  it("stops a past-only question at today", () => {
    renderItem(authoredItem("date", { config: { past_only: true } }))

    const today = new Date().toISOString().slice(0, 10)
    expect(screen.getByTestId("forms-date")).toHaveAttribute("max", today)
  })

  it("keeps the practice's own window when it is narrower than today", () => {
    renderItem(
      authoredItem("date", { config: { past_only: true, min: "2000-01-01", max: "2020-01-01" } }),
    )

    expect(screen.getByTestId("forms-date")).toHaveAttribute("min", "2000-01-01")
    expect(screen.getByTestId("forms-date")).toHaveAttribute("max", "2020-01-01")
  })

  it("shows the date on the review row", () => {
    const item = authoredItem("date")

    expect(rendererFor("date").summary({ value: "2026-03-14" }, item, INTAKE_FORM)).toBe(
      "2026-03-14",
    )
  })
})
