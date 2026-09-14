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
  | "plan"
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
 * What is true of how she is paid today. Several hold at once.
 *
 * Descriptive, not aspirational, and deliberately not a fork. This says where
 * her money comes from now; what she WANTS is asked once, separately, on the
 * plan screen. Folding the two together is what made the old single-select
 * router unable to describe a therapist who is on a platform AND takes a few
 * clients privately — which is an ordinary practice, not an edge case.
 */
export type CurrentStateId = "self_pay" | "platform" | "own_insurance"

export interface CurrentStateOption {
  id: CurrentStateId
  label: string
  detail: string
}

/**
 * Both halves in HER voice, as plain sentences with a subject.
 *
 * The platforms are NAMED because nobody says "I'm on a platform" — she says
 * "I'm on Headway". Recognition beats recall, and the list reads as knowing her
 * world. They appear only as examples of where her clients come from: never
 * compared to us, and never with leaving implied, because a therapist may
 * intend to stay on one indefinitely and that is a perfectly good answer.
 *
 * "Directly" used to be the first option's label and was ambiguous three ways —
 * direct payment, direct contracts, direct deposit. Superbills are called out
 * because out-of-network therapists are a large share of private pay and would
 * not otherwise know which line is theirs.
 */
export const CURRENT_STATES: readonly CurrentStateOption[] = [
  {
    id: "self_pay",
    label: "Clients pay me directly",
    detail: "By card, cash, or bank transfer. I may also give clients superbills.",
  },
  {
    id: "platform",
    label: "I see clients through a service like Headway, Alma, or Rula",
    detail: "The service handles their insurance and pays me.",
  },
  {
    id: "own_insurance",
    label: "I bill insurance myself",
    detail: "I have at least one contract in my own name and submit my own claims.",
  },
] as const

/** The question every therapist answers. */
const ROUTE_STEP: SetupStep = {
  id: "route",
  label: "How you're paid",
  caption: "Tell Pablo once. He'll take it from here.",
}

/**
 * What we are about to set up, and the one thing she might want that none of
 * it implies.
 *
 * Second, not last. It reads as a confirmation she would want anyway rather
 * than another question, which is what lets the credentialing ask cost no extra
 * screen — and it is her second look at the checklist, so a box she meant to
 * tick and didn't gets caught here for free.
 */
const PLAN_STEP: SetupStep = {
  id: "plan",
  label: "What we'll set up",
  caption: "Check this looks right before we start.",
}

/**
 * Every route collects practice details and rates: a superbill and a claim
 * need the same facts about who you are and what you charge. That shared spine
 * is why this is one wizard rather than two that happen to sit near each other.
 */
const PRACTICE_STEPS: readonly SetupStep[] = [
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

/**
 * The lookup that fills in the rest, which is why it goes first — for everyone,
 * including a therapist who bills nobody.
 *
 * The registry holds her legal name, credential, taxonomy, licence and
 * practice address — most of what the practice steps ask her to type. Asking
 * for those first and looking her up afterwards would mean making her type
 * what we were about to find, which is the product's whole argument backwards.
 *
 * Private pay needs it too, which is easy to miss. ``superbill.py`` lists
 * ``npi`` in ``_RENDERING_PROVIDER_REQUIRED``: without one we cannot produce a
 * superbill at all. A clinician who bills nobody still hands her client a
 * document that carries it, and finding that out when the client asks for her
 * reimbursement paperwork is the worst moment to find it out.
 *
 * It is not a gate anywhere. The screen answers "I don't have one" as a first
 * class outcome and lets her carry on, so asking early costs a clinician who
 * genuinely has no NPI one screen she can wave past.
 */
const LOOKUP_STEP: SetupStep = {
  id: "confirm",
  label: "What we found",
  caption: "Pablo looked these up. Just check them.",
}

const RECORD_STEP: SetupStep = {
  id: "record",
  label: "Your record",
  caption: "Answer once, reuse for every payer.",
}

/**
 * The steps she will actually walk, given what she ticked and what she asked
 * for.
 *
 * THE INVARIANT THIS FUNCTION EXISTS TO HOLD: every answer only ever ADDS
 * steps. Nothing here removes a step or sends her somewhere else. That is what
 * makes an under-answered checklist safe — and people do under-answer, ticking
 * whatever felt most true and moving on. A therapist on a platform who forgets
 * to mention her three cash clients gets correct platform setup and can add the
 * rest from Billing; she never lands somewhere wrong. Any future change that
 * makes a tick *remove* a step breaks that guarantee and the promise on screen
 * 1 that she can change this later.
 *
 * Being on a platform adds NOTHING to setup, and that is the point rather than
 * an oversight. Her platform already handles the insurance side; what the tick
 * buys her is restraint everywhere else — no payer step, no enrollment, no
 * clearinghouse record, nothing demanded that only an independent biller needs.
 *
 * Before she answers, only the opening is shown: a stepper that grew four
 * entries the moment she ticked a box would make answering feel like it cost
 * her something.
 */
export function stepsForState(
  state: readonly CurrentStateId[] | null,
  wantsCredentialing = false,
): readonly SetupStep[] {
  if (state === null) return [ROUTE_STEP]

  const steps: SetupStep[] = [ROUTE_STEP, PLAN_STEP, LOOKUP_STEP, ...PRACTICE_STEPS]

  // Which payers she can bill is a question for someone who bills insurers
  // herself, or is about to. It is never asked because she is on a platform:
  // the platform's payers are the platform's business.
  if (state.includes("own_insurance") || wantsCredentialing) steps.push(PAYER_STEP)
  // The credentialing record is only worth collecting from someone who asked
  // to be credentialed. Being out of network is not a request.
  if (wantsCredentialing) steps.push(RECORD_STEP)

  steps.push(DONE_STEP)
  return steps
}
