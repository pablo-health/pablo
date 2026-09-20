// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One renderer at a time: what it draws, what shape of answer it produces,
 * and what it puts on the review screen.
 *
 * The answer shapes are the load-bearing part. Each is checked against what
 * `backend/app/intake/answers.py` validates, because a renderer that builds
 * a plausible-looking value the save route refuses fails as a patient who
 * cannot get past a question.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { IntakeAssignmentItem } from "@/lib/api/patientIntake"
import { RENDERED_ITEM_TYPES, rendererFor } from "../renderers/registry"
import type { AnswerValue } from "../renderers/types"
import { INTAKE_FORM, ITEM_IDS, SEEDED_ITEMS } from "./formFixtures"

function itemOf(
  itemType: string,
  config: Record<string, unknown> = {},
  label: string | null = null,
  helpText: string | null = null,
): IntakeAssignmentItem {
  return {
    id: "00000000-0000-4000-8000-000000000000",
    key: itemType,
    position: 0,
    item_type: itemType,
    required: true,
    label,
    help_text: helpText,
    config,
    value: null,
  }
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

/**
 * What the walk hands a renderer that writes through a route of its own.
 *
 * Inert here: every renderer in this file collects a value and calls
 * `onChange`. The consent document is the exception and has its own file,
 * where these are wired to a stubbed client.
 */
const ROUTE_PROPS = {
  assignmentId: "00000000-0000-4000-8000-00000000000a",
  sessionToken: "session-token",
  onWrote: () => {},
  onSessionLost: () => {},
}

describe("the registry", () => {
  it("draws every question but the ones that need a file or a screen of their own", () => {
    expect(RENDERED_ITEM_TYPES.sort()).toEqual([
      "consent_document",
      "date",
      "demographics",
      "free_text",
      "instructions",
      "instrument",
      "multi_choice",
      "number",
      "reason",
      "scale",
      "section",
      "single_choice",
      "yes_no",
    ])
  })

  it("says a question it cannot ask is a step still to come", () => {
    renderItem(itemOf("insurance_card", { sides: "both" }, "A photo of your card"))

    expect(screen.getByTestId("forms-item-unavailable")).toHaveTextContent(
      "This step will be available soon.",
    )
  })

  it("keeps an unaskable question off the review screen", () => {
    expect(rendererFor("document_request").answerable).toBe(false)
    expect(rendererFor("insurance_card").answerable).toBe(false)
    expect(rendererFor("document_request").writesItself).toBeUndefined()
    expect(rendererFor("insurance_card").writesItself).toBeUndefined()
  })

  it("counts a consent document as a question even though the walk cannot save it", () => {
    // Its answer names a signature row, so the save route refuses the type
    // outright — but it is still a question, so it is counted and reviewed.
    expect(rendererFor("consent_document").answerable).toBe(false)
    expect(rendererFor("consent_document").writesItself).toBe(true)
  })
})

describe("demographics", () => {
  it("confirms both fields with one tap", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(SEEDED_ITEMS[0])

    await user.click(screen.getByTestId("forms-identity-confirm"))

    expect(onChange).toHaveBeenCalledWith({
      name_confirmed: true,
      dob_confirmed: true,
      corrections: null,
    })
  })

  it("opens a box to say what is wrong, and carries on either way", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(SEEDED_ITEMS[0], {
      name_confirmed: false,
      dob_confirmed: false,
      corrections: null,
    })

    await user.type(screen.getByTestId("forms-corrections"), "M")

    expect(onChange).toHaveBeenCalledWith({
      name_confirmed: false,
      dob_confirmed: false,
      corrections: "M",
    })
  })

  it("shows the chart's name and date of birth to confirm", () => {
    renderItem(SEEDED_ITEMS[0])

    expect(screen.getByTestId("forms-identity-name")).toHaveTextContent("Dana Okonkwo")
    expect(screen.getByTestId("forms-identity-dob")).toHaveTextContent("1988-04-02")
  })

  it("says whether something was flagged, and never what", () => {
    const renderer = rendererFor("demographics")
    const flagged = { name_confirmed: false, dob_confirmed: false, corrections: "Wrong surname" }

    const summary = renderer.summary(flagged, SEEDED_ITEMS[0], INTAKE_FORM)

    expect(summary).toBe("Something to correct")
    expect(summary).not.toContain("Wrong surname")
  })
})

describe("reason", () => {
  it("asks the server's prompt and stores the text under `text`", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(SEEDED_ITEMS[1])

    expect(screen.getByRole("heading", { name: "What brings you in?" })).toBeInTheDocument()
    await user.type(screen.getByTestId("forms-reason"), "S")

    expect(onChange).toHaveBeenCalledWith({ text: "S" })
  })
})

describe("instrument", () => {
  it("renders the server's items and anchors verbatim", () => {
    renderItem(SEEDED_ITEMS[2])

    expect(screen.getByRole("heading", { name: "PHQ-9" })).toBeInTheDocument()
    expect(
      screen.getByText("Little interest or pleasure in doing things"),
    ).toBeInTheDocument()
    const group = screen.getByTestId("forms-item-phq9-1")
    expect(within(group).getByRole("radio", { name: "Nearly every day" })).toBeInTheDocument()
  })

  it("stores item scores keyed the way the scorer reads them", async () => {
    const user = userEvent.setup()
    const { onChange } = renderItem(SEEDED_ITEMS[2], { item_scores: { "1": 0 } })

    const group = screen.getByTestId("forms-item-phq9-2")
    await user.click(within(group).getByRole("radio", { name: "Several days" }))

    expect(onChange).toHaveBeenCalledWith({ item_scores: { "1": 0, "2": 1 } })
  })

  it("counts answers on the review screen and reports no total", () => {
    const renderer = rendererFor("instrument")

    const summary = renderer.summary({ item_scores: { "1": 3, "2": 3 } }, SEEDED_ITEMS[2], INTAKE_FORM)

    expect(summary).toBe("2 of 9 answered")
  })

  it("says the step is to come when this deployment sent no wording for it", () => {
    renderItem(itemOf("instrument", { code: "audit" }))

    expect(screen.getByTestId("forms-item-unavailable")).toBeInTheDocument()
  })
})

describe("display-only items", () => {
  it("shows a section's title and collects nothing", () => {
    renderItem(itemOf("section", { title: "About you" }))

    expect(screen.getByTestId("forms-item-section")).toHaveTextContent("About you")
    expect(rendererFor("section").answerable).toBe(false)
  })

  it("keeps the line breaks in a block of instructions", () => {
    renderItem(itemOf("instructions", { body_markdown: "Take your time.\n\nThere is no rush." }))

    expect(screen.getByTestId("forms-item-instructions")).toHaveTextContent("Take your time.")
    expect(rendererFor("instructions").answerable).toBe(false)
  })
})

describe("what the review screen is told", () => {
  it("has nothing to show for a question nobody has answered", () => {
    for (const item of SEEDED_ITEMS) {
      expect(rendererFor(item.item_type).summary(null, item, INTAKE_FORM)).toBeNull()
    }
    expect(ITEM_IDS.demographics).toBeTruthy()
  })
})
