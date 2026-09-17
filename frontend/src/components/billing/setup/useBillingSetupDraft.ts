// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useReducer } from "react"
import type { UserPreferences } from "@/lib/api/users"
import type { CurrentStateId } from "./routes"
import { HAS_PAYMENTS_SETUP } from "./setupSlots.extensions"

/**
 * Her answers to billing setup, and where she had got to.
 *
 * Two copies of this exist at any moment — what is SAVED on her preferences,
 * and what she has changed in this SITTING — and the screen must render the
 * second laid over the first. That merge used to be open-coded once per field:
 *
 *     const activeState = state ?? savedState
 *     const activeWants = wants ?? savedWants
 *     const activeWantsCard = HAS_PAYMENTS_SETUP && (wantsCard ?? savedCard ?? defaultCard)
 *
 * Three near-identical lines, four `useState`s, and a rule that got quietly
 * more complicated each time a question was added — the last one grew both a
 * capability gate and a computed default. Adding a fourth answer meant writing
 * the merge a fourth time and hoping it matched.
 *
 * So: one draft, one reducer, and the merge as a pure function that can be
 * tested without rendering anything.
 *
 * This is deliberately NOT a state machine. Which screens she walks is already
 * derived — `stepsForState` is a pure function of her answers — and that is the
 * part a statechart would otherwise model. What is left is a form whose values
 * are edited locally and persisted as she goes, which is what `useReducer` is
 * for. A statechart here would add a dependency and a vocabulary in exchange
 * for describing transitions that do not exist.
 */

/** What her saved preferences say, narrowed to what setup cares about. */
export interface SavedAnswers {
  state: CurrentStateId[] | null
  wantsCredentialing: boolean
  /** `null` means she has never said, which is what lets a default apply. */
  wantsCardPayments: boolean | null
  step: string | null
}

/**
 * What she has changed since the page loaded.
 *
 * Every field optional, and absent means "no opinion this sitting" — which is
 * what lets the saved value show through. `undefined` is doing real work here
 * and is not the same as `false`.
 */
export interface SetupDraft {
  state?: CurrentStateId[]
  wantsCredentialing?: boolean
  wantsCardPayments?: boolean
  step?: string
}

/** The answers actually rendered: draft over saved over default. */
export interface ResolvedAnswers {
  state: CurrentStateId[] | null
  wantsCredentialing: boolean
  wantsCardPayments: boolean
  step: string
}

export type SetupDraftAction =
  | { type: "toggleState"; id: CurrentStateId }
  | { type: "answerChecklist"; state: CurrentStateId[] }
  | { type: "wantsCredentialing"; value: boolean }
  | { type: "wantsCardPayments"; value: boolean }
  | { type: "goTo"; step: string }

export function setupDraftReducer(draft: SetupDraft, action: SetupDraftAction): SetupDraft {
  switch (action.type) {
    case "toggleState": {
      // Ticking is not answering. Nothing here is persisted until Continue, so
      // a box tried and untried again leaves no trace — and the checklist is
      // the one screen where she is most likely to change her mind mid-thought.
      const base = draft.state ?? []
      return {
        ...draft,
        state: base.includes(action.id)
          ? base.filter((x) => x !== action.id)
          : [...base, action.id],
      }
    }
    case "answerChecklist":
      return { ...draft, state: action.state, step: "plan" }
    case "wantsCredentialing":
      return { ...draft, wantsCredentialing: action.value }
    case "wantsCardPayments":
      return { ...draft, wantsCardPayments: action.value }
    case "goTo":
      return { ...draft, step: action.step }
  }
}

/**
 * Lay this sitting over what was saved, and apply the one default that exists.
 *
 * Pure, and exported for its own sake: this is the rule the whole screen hangs
 * on, and it is far cheaper to assert directly than through a rendered wizard.
 *
 * `canTakeCardPayments` is a parameter rather than a direct read of
 * `HAS_PAYMENTS_SETUP` so a test can exercise both deployments. It gates the
 * resolved answer, not just the screen: a deployment that cannot connect a
 * processor must never resolve to wanting one, or `stepsForState` appends a
 * step whose body renders nothing.
 */
export function resolveAnswers(
  saved: SavedAnswers,
  draft: SetupDraft,
  { canTakeCardPayments = HAS_PAYMENTS_SETUP }: { canTakeCardPayments?: boolean } = {},
): ResolvedAnswers {
  const state = draft.state ?? saved.state

  // A self-pay practice arrives wanting this, because screen 1's self-pay
  // option reads "Card, cash, bank transfer" — she has already said she takes
  // card. A DEFAULT, not a decision: anything she has actually said, in this
  // sitting or a previous one, wins over it. That is why the saved value is
  // nullable; a `false` default would be indistinguishable from her having
  // declined, and unticking the box would not survive the next page load.
  const cardDefault = (state ?? []).includes("self_pay")

  return {
    state,
    wantsCredentialing: draft.wantsCredentialing ?? saved.wantsCredentialing,
    wantsCardPayments:
      canTakeCardPayments && (draft.wantsCardPayments ?? saved.wantsCardPayments ?? cardDefault),
    step: draft.step ?? saved.step ?? "route",
  }
}

/** Read the saved half off her preferences. */
export function savedAnswersFrom(preferences: UserPreferences | undefined): SavedAnswers {
  return {
    state: (preferences?.billing_setup_state ?? null) as CurrentStateId[] | null,
    wantsCredentialing: preferences?.billing_setup_wants_credentialing ?? false,
    wantsCardPayments: preferences?.billing_setup_wants_card_payments ?? null,
    step: preferences?.billing_setup_step ?? null,
  }
}

/**
 * What an action writes to her preferences, or nothing if it writes none.
 *
 * A patch rather than one key per action, because answering the checklist
 * moves her on in the same breath and BOTH halves have to land. Persisting the
 * answer without the step would leave her back on screen 1 next time, having
 * answered it.
 *
 * Exported so the mapping can be asserted directly — a wrong key here is
 * invisible on screen and only shows up as work that silently did not save.
 */
export function persistedPatch(action: SetupDraftAction): Partial<UserPreferences> | null {
  switch (action.type) {
    // Deliberately nothing: see the reducer. Ticking is not answering.
    case "toggleState":
      return null
    case "answerChecklist":
      return { billing_setup_state: action.state, billing_setup_step: "plan" }
    case "wantsCredentialing":
      return { billing_setup_wants_credentialing: action.value }
    case "wantsCardPayments":
      return { billing_setup_wants_card_payments: action.value }
    case "goTo":
      return { billing_setup_step: action.step }
  }
}

/**
 * The draft, and one way to change it.
 *
 * `apply` both updates the draft and persists it, because those were two calls
 * at every call site and two calls can drift — a toggle that set state without
 * remembering it looked correct on screen and silently did not save, which is
 * the failure this hook exists to make unrepresentable.
 *
 * `toggleState` is the one action that deliberately does NOT persist; see the
 * reducer.
 */
export function useBillingSetupDraft(
  preferences: UserPreferences | undefined,
  save: (next: UserPreferences) => void,
) {
  const [draft, dispatch] = useReducer(setupDraftReducer, {})
  const saved = savedAnswersFrom(preferences)
  const answers = resolveAnswers(saved, draft)

  const apply = useCallback(
    (action: SetupDraftAction) => {
      dispatch(action)
      if (!preferences) return
      const patch = persistedPatch(action)
      if (patch) save({ ...preferences, ...patch })
    },
    [preferences, save],
  )

  const settle = useCallback(() => {
    if (!preferences) return
    save({ ...preferences, billing_setup_complete: true })
  }, [preferences, save])

  return { answers, apply, settle }
}
