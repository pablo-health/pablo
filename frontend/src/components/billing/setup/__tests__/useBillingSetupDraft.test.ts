// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The merge rule, asserted directly.
 *
 * This is the whole reason the rule moved out of the component: it is the
 * thing the screen hangs on, it has one genuine default and one capability
 * gate, and none of that needs a rendered wizard to check. Every case below
 * used to be reachable only by driving the UI.
 */

import { describe, expect, it } from "vitest"
import type { SavedAnswers } from "../useBillingSetupDraft"
import { persistedPatch, resolveAnswers, setupDraftReducer } from "../useBillingSetupDraft"

const NOTHING_SAVED: SavedAnswers = {
  state: null,
  wantsCredentialing: false,
  wantsCardPayments: null,
  step: null,
}

const CAN_TAKE_CARD = { canTakeCardPayments: true }

describe("laying this sitting over what was saved", () => {
  it("shows the saved answer when she has changed nothing", () => {
    const saved: SavedAnswers = {
      state: ["platform"],
      wantsCredentialing: true,
      wantsCardPayments: false,
      step: "rates",
    }
    expect(resolveAnswers(saved, {}, CAN_TAKE_CARD)).toEqual({
      state: ["platform"],
      wantsCredentialing: true,
      wantsCardPayments: false,
      step: "rates",
    })
  })

  it("lets this sitting win, so the screen reacts before the save lands", () => {
    const saved: SavedAnswers = { ...NOTHING_SAVED, state: ["platform"], step: "rates" }
    const resolved = resolveAnswers(saved, { state: ["own_insurance"], step: "payers" }, CAN_TAKE_CARD)
    expect(resolved.state).toEqual(["own_insurance"])
    expect(resolved.step).toEqual("payers")
  })

  it("starts at the first screen when nothing is saved and nothing drafted", () => {
    expect(resolveAnswers(NOTHING_SAVED, {}).step).toBe("route")
  })
})

describe("wanting card payments", () => {
  it("is assumed for a self-pay practice, because she already said so", () => {
    // Screen 1's self-pay option reads "Card, cash, bank transfer".
    expect(resolveAnswers({ ...NOTHING_SAVED, state: ["self_pay"] }, {}, CAN_TAKE_CARD)
      .wantsCardPayments).toBe(true)
  })

  it("is not assumed for anyone else", () => {
    for (const state of [["platform"], ["own_insurance"], []] as const) {
      expect(resolveAnswers({ ...NOTHING_SAVED, state: [...state] }, {}, CAN_TAKE_CARD)
        .wantsCardPayments).toBe(false)
    }
  })

  it("SURVIVES being unticked by a self-pay practice", () => {
    // The whole reason the stored value is nullable. A `false` default would
    // be indistinguishable from having declined, and the assumption above
    // would switch it back on every time she reopened the wizard — bringing
    // back the step she just declined.
    const saved: SavedAnswers = {
      ...NOTHING_SAVED,
      state: ["self_pay"],
      wantsCardPayments: false,
    }
    expect(resolveAnswers(saved, {}, CAN_TAKE_CARD).wantsCardPayments).toBe(false)
  })

  it("is never true where the deployment cannot connect a processor", () => {
    // Otherwise `stepsForState` appends a step whose body renders nothing, and
    // she clicks through to a blank screen.
    const saved: SavedAnswers = { ...NOTHING_SAVED, state: ["self_pay"], wantsCardPayments: true }
    expect(resolveAnswers(saved, { wantsCardPayments: true }, { canTakeCardPayments: false })
      .wantsCardPayments).toBe(false)
  })
})

describe("ticking the checklist", () => {
  it("adds and removes without touching anything else", () => {
    const once = setupDraftReducer({}, { type: "toggleState", id: "self_pay" })
    expect(once.state).toEqual(["self_pay"])
    const twice = setupDraftReducer(once, { type: "toggleState", id: "self_pay" })
    expect(twice.state).toEqual([])
  })

  it("is NOT persisted, so a box tried and untried leaves no trace", () => {
    expect(persistedPatch({ type: "toggleState", id: "self_pay" })).toBeNull()
  })
})

describe("what each action writes", () => {
  it("saves the answer AND the step when she answers the checklist", () => {
    // Persisting one without the other would put her back on screen 1 next
    // time, having already answered it.
    expect(persistedPatch({ type: "answerChecklist", state: ["platform"] })).toEqual({
      billing_setup_state: ["platform"],
      billing_setup_step: "plan",
    })
  })

  it("records an empty checklist as a real answer", () => {
    // "Not seeing clients yet" is an answer, and must never read back as
    // "has not answered".
    expect(persistedPatch({ type: "answerChecklist", state: [] })?.billing_setup_state).toEqual([])
  })

  it("writes each want under its own key", () => {
    expect(persistedPatch({ type: "wantsCredentialing", value: true })).toEqual({
      billing_setup_wants_credentialing: true,
    })
    expect(persistedPatch({ type: "wantsCardPayments", value: false })).toEqual({
      billing_setup_wants_card_payments: false,
    })
  })
})
