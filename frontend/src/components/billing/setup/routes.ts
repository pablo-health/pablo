// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every step this wizard can show.
 *
 * A union rather than loose strings, so the table of step bodies can be typed
 * ``Record<StepId, ...>``: a step added here without a body fails to compile,
 * and a body for a step that no longer exists fails too. That check used to be
 * a runtime test, which only caught it once somebody walked the flow.
 */
export type StepId =
  | "route"
  | "identity"
  | "contact"
  | "rates"
  | "payers"
  | "confirm"
  | "record"
  | "done"

/**
 * A step, narrowed to the ids this wizard knows. Satisfies the shell's shape.
 *
 * ``caption`` is what Pablo says beside this step. Per-step rather than one
 * line for the whole wizard, because a caption that is true of every screen
 * says nothing about any of them.
 */
export interface SetupStep {
  id: StepId
  label: string
  caption: string
}

/**
 * The four answers to "how are you getting paid right now?", and what each one
 * makes the rest of the wizard.
 *
 * Situational, not aspirational. Every option describes what is true today
 * rather than what she hopes for, because the routing depends on the former
 * and she can only be sure of the former. Asking what she *wants* would invite
 * an answer about the next six months and route her on it.
 */
export type PaymentRouteId =
  | "private_pay"
  | "already_paneled"
  | "wants_panels"
  | "platform_to_own"

export interface PaymentRoute {
  id: PaymentRouteId
  label: string
  detail: string
}

//: Both halves are written in HER voice, not the product's. A label she would
//: say about herself, explained in a sentence she would also say, makes
//: choosing feel like self-description rather than being sorted into a bucket.
export const PAYMENT_ROUTES: readonly PaymentRoute[] = [
  {
    id: "private_pay",
    label: "My clients pay me directly",
    detail:
      "Clients pay by card, bank transfer, or on a sliding scale. I don’t bill insurance.",
  },
  {
    id: "already_paneled",
    label: "I’m already on insurance panels",
    detail: "I’m contracted with at least one insurer and can bill them directly.",
  },
  {
    id: "wants_panels",
    label: "I want to accept insurance, but I’m not on a panel yet",
    detail:
      "I’d like help applying to insurers and managing the credentialing process.",
  },
  {
    id: "platform_to_own",
    label: "I see clients through a platform and want my own contracts",
    detail:
      "I want insurer contracts in my own name, so I can build an independent practice.",
  },
] as const

/**
 * Every route collects practice details and rates: a superbill and a claim
 * need the same facts about who you are and what you charge. That shared spine
 * is why this is one wizard rather than two that happen to sit near each other.
 */
const SHARED_STEPS: readonly SetupStep[] = [
  { id: "route", label: "How you're paid", caption: "Tell Pablo once. He'll take it from here." },
  { id: "identity", label: "Practice identity", caption: "Pablo keeps the paperwork straight." },
  { id: "contact", label: "Billing contact", caption: "So billing messages reach the right place." },
  { id: "rates", label: "Your rates", caption: "What a session is worth, in one place." },
]

//: Every branch ends somewhere it says what happens next, rather than stopping.
const DONE_STEP: SetupStep = {
  id: "done",
  label: "Done",
  caption: "That's everything Pablo needs.",
}

const PAYER_STEP: SetupStep = {
  id: "payers",
  label: "Payers",
  caption: "Who you can bill, and who you can't yet.",
}
const RECORD_STEPS: readonly SetupStep[] = [
  { id: "confirm", label: "What we found", caption: "Pablo looked these up. Just check them." },
  { id: "record", label: "Your record", caption: "Answer once, reuse for every payer." },
]

/**
 * The steps this clinician will actually walk, given her answer.
 *
 * Before she answers, only the shared spine is shown — a stepper that grew
 * three entries the moment she clicked would make the choice feel like it cost
 * her something.
 */
export function stepsForRoute(route: PaymentRouteId | null): readonly SetupStep[] {
  if (route === null) return SHARED_STEPS
  if (route === "private_pay") return [...SHARED_STEPS, DONE_STEP]
  if (route === "already_paneled") return [...SHARED_STEPS, PAYER_STEP, DONE_STEP]
  return [...SHARED_STEPS, PAYER_STEP, ...RECORD_STEPS, DONE_STEP]
}
