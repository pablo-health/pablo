// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * "Show only when" — the picker that writes a visibility rule.
 *
 * Two things are asserted throughout. The conditions offered come from the
 * earlier question's own type, so a measure offers a score and a tick-list
 * offers its answers. And what the picker emits is the shape the server
 * stores, because a rename here would otherwise produce a rule the publish
 * endpoint rejects and no test would notice.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { VisibilityRuleForm, type VisibilityTarget } from "../VisibilityRuleForm"
import {
  VISIBILITY_ALWAYS,
  VISIBILITY_CONDITION_LABEL,
  VISIBILITY_NOTHING_EARLIER,
  VISIBILITY_QUESTION_LABEL,
  VISIBILITY_VALUE_LABEL,
} from "../intakeCopy"
import type { VisibleWhen } from "@/lib/intake/visibility"

const onChange = vi.fn()

const DRINKS: VisibilityTarget = {
  key: "drinks",
  item_type: "single_choice",
  label: "Do you drink alcohol?",
  config: {
    options: [
      { key: "yes", label: "Yes" },
      { key: "no", label: "No" },
    ],
  },
}

const SMOKES: VisibilityTarget = {
  key: "smokes",
  item_type: "yes_no",
  label: "Do you smoke?",
  config: {},
}

const PHQ9: VisibilityTarget = {
  key: "phq9",
  item_type: "instrument",
  label: null,
  config: { code: "phq9" },
}

const BIRTHDAY: VisibilityTarget = {
  key: "date_of_birth",
  item_type: "date",
  label: "Date of birth",
  config: {},
}

function picker(earlier: VisibilityTarget[], rule: VisibleWhen | null = null) {
  return render(
    <VisibilityRuleForm
      rule={rule}
      earlier={earlier}
      onChange={onChange}
      idPrefix="test"
    />,
  )
}

/** The rule the last onChange carried. */
function lastRule(): VisibleWhen | null {
  return onChange.mock.calls[onChange.mock.calls.length - 1][0]
}

describe("VisibilityRuleForm", () => {
  beforeEach(() => vi.clearAllMocks())

  it("says so on the first question, which has nothing to depend on", () => {
    picker([])
    expect(screen.getByText(VISIBILITY_NOTHING_EARLIER)).toBeInTheDocument()
  })

  it("offers only the questions above this one", async () => {
    const user = userEvent.setup()
    picker([DRINKS, SMOKES])

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_QUESTION_LABEL }))

    expect(screen.getByRole("option", { name: VISIBILITY_ALWAYS })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Do you drink alcohol?" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "Do you smoke?" })).toBeInTheDocument()
  })

  it("names a question the practice has not worded yet by its kind", async () => {
    const user = userEvent.setup()
    picker([PHQ9])

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_QUESTION_LABEL }))

    expect(screen.getByRole("option", { name: "Measure" })).toBeInTheDocument()
  })

  it("starts a rule off as 'answered it at all' when a question is picked", async () => {
    const user = userEvent.setup()
    picker([DRINKS])

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_QUESTION_LABEL }))
    await user.click(screen.getByRole("option", { name: "Do you drink alcohol?" }))

    expect(lastRule()).toEqual({ item_key: "drinks", op: "answered" })
  })

  it("takes the rule off again", async () => {
    const user = userEvent.setup()
    picker([DRINKS], { item_key: "drinks", op: "answered" })

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_QUESTION_LABEL }))
    await user.click(screen.getByRole("option", { name: VISIBILITY_ALWAYS }))

    expect(lastRule()).toBeNull()
  })

  it("offers a choice question's own answers", async () => {
    const user = userEvent.setup()
    picker([DRINKS], { item_key: "drinks", op: "eq", value: "yes" })

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_VALUE_LABEL }))

    expect(screen.getByRole("option", { name: "Yes" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "No" })).toBeInTheDocument()
  })

  it("writes the answer's key, not its wording", async () => {
    const user = userEvent.setup()
    picker([DRINKS], { item_key: "drinks", op: "eq", value: "no" })

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_VALUE_LABEL }))
    await user.click(screen.getByRole("option", { name: "Yes" }))

    expect(lastRule()).toEqual({ item_key: "drinks", op: "eq", value: "yes" })
  })

  it("writes a yes as a yes rather than as a word", async () => {
    const user = userEvent.setup()
    picker([SMOKES], { item_key: "smokes", op: "answered" })

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_CONDITION_LABEL }))
    await user.click(screen.getByRole("option", { name: "said yes" }))

    expect(lastRule()).toEqual({ item_key: "smokes", op: "eq", value: true })
  })

  it("offers a measure its score rather than its answers", async () => {
    const user = userEvent.setup()
    picker([PHQ9], { item_key: "phq9", op: "answered" })

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_CONDITION_LABEL }))

    expect(screen.getByRole("option", { name: "scored at least" })).toBeInTheDocument()
    expect(screen.getByRole("option", { name: "answered one question at least" })).toBeInTheDocument()
    expect(screen.queryByRole("option", { name: "picked" })).not.toBeInTheDocument()
  })

  it("writes a score threshold as a number", async () => {
    const user = userEvent.setup()
    picker([PHQ9], { item_key: "phq9", op: "score_gte", value: 1 })

    await user.clear(screen.getByLabelText(VISIBILITY_VALUE_LABEL))
    await user.type(screen.getByLabelText(VISIBILITY_VALUE_LABEL), "10")

    expect(lastRule()).toEqual({ item_key: "phq9", op: "score_gte", value: 10 })
  })

  it("writes one measure item as an item number and a score", async () => {
    const user = userEvent.setup()
    picker([PHQ9], { item_key: "phq9", op: "item_gte", value: { item: 9 } })

    await user.type(screen.getByLabelText(VISIBILITY_VALUE_LABEL), "1")

    expect(lastRule()).toEqual({
      item_key: "phq9",
      op: "item_gte",
      value: { item: 9, value: 1 },
    })
  })

  it("offers a date question a date to compare against", async () => {
    const user = userEvent.setup()
    picker([BIRTHDAY], { item_key: "date_of_birth", op: "answered" })

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_CONDITION_LABEL }))
    await user.click(screen.getByRole("option", { name: "gave a date on or after" }))

    expect(lastRule()).toEqual({
      item_key: "date_of_birth",
      op: "gte",
      value: undefined,
    })
  })

  it("offers a written answer nothing but whether it was answered", async () => {
    const user = userEvent.setup()
    const reason: VisibilityTarget = {
      key: "reason",
      item_type: "reason",
      label: null,
      config: {},
    }
    picker([reason], { item_key: "reason", op: "answered" })

    await user.click(screen.getByRole("combobox", { name: VISIBILITY_CONDITION_LABEL }))

    expect(screen.getAllByRole("option")).toHaveLength(1)
    expect(screen.getByRole("option", { name: "answered it at all" })).toBeInTheDocument()
  })
})
