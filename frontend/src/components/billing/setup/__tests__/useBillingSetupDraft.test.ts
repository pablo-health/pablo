// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The merge rule, asserted directly.
 *
 * This is the whole reason the rule moved out of the component: it is the
 * thing the screen hangs on, it has one genuine default and one capability
 * gate, and none of that needs a rendered wizard to check. Every case below
 * used to be reachable only by driving the UI.
 */

import { act, renderHook } from "@testing-library/react"
import { describe, expect, it, vi } from "vitest"
import type { UserPreferences } from "@/lib/api/users"
import type { SavedAnswers } from "../useBillingSetupDraft"
import {
  persistedPatch,
  resolveAnswers,
  setupDraftReducer,
  useBillingSetupDraft,
} from "../useBillingSetupDraft"

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

/**
 * A stand-in for the preferences endpoint: a full replace, with replies the
 * test releases by hand so it chooses the order saves land in.
 */
function fakePreferencesEndpoint(initial: UserPreferences) {
  let stored = initial
  const pending: { body: UserPreferences; land: () => void; refuse: () => void }[] = []
  let mostInFlight = 0
  const save = vi.fn(
    (body: UserPreferences) =>
      new Promise<UserPreferences>((resolve, reject) => {
        pending.push({
          body,
          land: () => {
            stored = body
            resolve(body)
          },
          refuse: () => reject(new Error("refused")),
        })
        mostInFlight = Math.max(mostInFlight, pending.length)
      }),
  )
  return {
    save,
    stored: () => stored,
    mostInFlight: () => mostInFlight,
    /** Land whatever is in flight, newest first, until nothing is left. */
    async landNewestFirst() {
      while (pending.length > 0) {
        const newest = pending.pop()!
        await act(async () => newest.land())
      }
    },
    async refuseOldest() {
      const oldest = pending.shift()!
      await act(async () => oldest.refuse())
    },
  }
}

const LOADED = { timezone: "America/Chicago", theme: "warm-paper" } as UserPreferences

describe("saving answers as they are given", () => {
  it("keeps the step reached when two answers are saved back to back", async () => {
    // Answer the checklist, then Continue again straight away. Each save used
    // to be a full copy of the preferences as that render saw them, so the
    // second carried the empty checklist from before the first had landed and
    // erased the answer — and the two were in flight together, so the older
    // could land last and move the step back.
    const endpoint = fakePreferencesEndpoint(LOADED)
    const { result } = renderHook(() => useBillingSetupDraft(LOADED, endpoint.save))

    act(() => result.current.apply({ type: "answerChecklist", state: ["self_pay"] }))
    act(() => result.current.apply({ type: "goTo", step: "confirm" }))
    await endpoint.landNewestFirst()

    expect(endpoint.mostInFlight()).toBe(1)
    expect(endpoint.stored()).toMatchObject({
      timezone: "America/Chicago",
      billing_setup_state: ["self_pay"],
      billing_setup_step: "confirm",
    })
  })

  it("holds answers given before preferences load, then saves them", async () => {
    // A full replace built on nothing would erase every other preference, so
    // nothing may go out early — but dropping the answer is no better.
    const endpoint = fakePreferencesEndpoint(LOADED)
    const { result, rerender } = renderHook(
      ({ preferences }: { preferences: UserPreferences | undefined }) =>
        useBillingSetupDraft(preferences, endpoint.save),
      { initialProps: { preferences: undefined as UserPreferences | undefined } },
    )

    act(() => result.current.apply({ type: "answerChecklist", state: ["self_pay"] }))
    act(() => result.current.apply({ type: "goTo", step: "confirm" }))
    expect(endpoint.save).not.toHaveBeenCalled()

    rerender({ preferences: LOADED })
    await endpoint.landNewestFirst()

    expect(endpoint.stored()).toMatchObject({
      timezone: "America/Chicago",
      billing_setup_state: ["self_pay"],
      billing_setup_step: "confirm",
    })
  })

  it("carries an answer whose save was refused on the next save", async () => {
    const endpoint = fakePreferencesEndpoint(LOADED)
    const { result } = renderHook(() => useBillingSetupDraft(LOADED, endpoint.save))

    act(() => result.current.apply({ type: "answerChecklist", state: ["platform"] }))
    await endpoint.refuseOldest()
    act(() => result.current.apply({ type: "goTo", step: "confirm" }))
    await endpoint.landNewestFirst()

    expect(endpoint.stored()).toMatchObject({
      billing_setup_state: ["platform"],
      billing_setup_step: "confirm",
    })
  })
})
