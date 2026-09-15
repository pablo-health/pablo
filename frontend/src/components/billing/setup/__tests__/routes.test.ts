// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Which steps a therapist walks, given what she ticked.
 *
 * The bug class this file exists for is a **wrong path**, which is far worse
 * than a missing one. A missed step she can add from Billing; a route that
 * quietly decided something about her practice is one she has no way to
 * notice. So the central test here is the invariant rather than any particular
 * sequence: every answer only ever ADDS.
 *
 * That is what makes the checklist safe to under-answer, and people do
 * under-answer — they tick whatever felt most true and move on. If a future
 * change makes a tick remove a step, the guarantee on screen 1 that she can
 * change this later stops being true, and these tests should be what says so.
 */

import { describe, expect, it } from "vitest"
import { CURRENT_STATES, type CurrentStateId, stepsForState } from "../routes"

const ids = (state: CurrentStateId[] | null, wants = false, wantsCard = false) =>
  stepsForState(state, wants, wantsCard).map((s) => s.id)

const ALL: CurrentStateId[] = ["self_pay", "platform", "own_insurance"]

/** Every combination of ticks, including none. */
function everySubset(): CurrentStateId[][] {
  const out: CurrentStateId[][] = []
  for (let mask = 0; mask < 1 << ALL.length; mask++) {
    out.push(ALL.filter((_, i) => mask & (1 << i)))
  }
  return out
}

describe("before she answers", () => {
  it("shows only the question itself", () => {
    // A stepper that grew four entries the moment she ticked a box would make
    // answering feel like it cost her something.
    expect(ids(null)).toEqual(["route"])
  })
})

describe("the invariant: ticks only ever add", () => {
  it.each(everySubset().map((s) => [s.join("+") || "(none)", s] as const))(
    "adding a tick to %s never removes a step",
    (_label, state) => {
      for (const extra of ALL) {
        if (state.includes(extra)) continue
        const before = ids(state)
        const after = ids([...state, extra])
        for (const step of before) expect(after).toContain(step)
      }
    },
  )

  it.each(everySubset().map((s) => [s.join("+") || "(none)", s] as const))(
    "asking for credentialing from %s never removes a step",
    (_label, state) => {
      const before = ids(state)
      const after = ids(state, true)
      for (const step of before) expect(after).toContain(step)
    },
  )

  it.each(everySubset().map((s) => [s.join("+") || "(none)", s] as const))(
    "%s keeps the shared spine, whatever she ticked",
    (_label, state) => {
      // A superbill and a claim need the same facts, so these are on every
      // path — including the one for a therapist who bills nobody.
      for (const step of ["route", "plan", "confirm", "identity", "contact", "rates", "done"]) {
        expect(ids(state)).toContain(step)
      }
    },
  )
})

describe("what each answer adds", () => {
  it("being on a platform adds nothing to setup", () => {
    // The point of the tick, not an oversight: her platform already handles
    // the insurance side, so what it buys her is restraint everywhere else.
    expect(ids(["platform"])).toEqual(ids([]))
  })

  it("billing insurance herself adds the payer step", () => {
    expect(ids(["own_insurance"])).toContain("payers")
    expect(ids(["self_pay"])).not.toContain("payers")
  })

  it("asking to be credentialed adds the payer and record steps", () => {
    const asked = ids(["self_pay"], true)
    expect(asked).toContain("payers")
    expect(asked).toContain("record")
  })

  it("does not collect a credentialing record from someone who did not ask", () => {
    // Being out of network is not a request for help getting in network.
    for (const state of everySubset()) {
      expect(ids(state)).not.toContain("record")
    }
  })

  it("never asks a platform clinician about payers just for being on one", () => {
    // The platform's payers are the platform's business.
    expect(ids(["platform"])).not.toContain("payers")
    expect(ids(["self_pay", "platform"])).not.toContain("payers")
  })
})

describe("the platform case end to end", () => {
  it("a platform clinician taking cash walks the cash-pay flow and nothing more", () => {
    // The case the whole redesign exists for, and the one the old router could
    // not express at all: she keeps the platform AND takes clients privately.
    expect(ids(["self_pay", "platform"])).toEqual([
      "route",
      "plan",
      "confirm",
      "identity",
      "contact",
      "rates",
      "done",
    ])
  })

  it("is the same flow a cash-only practice walks, which is the point", () => {
    // Cash-pay-only is a strict subset of the platform case. If the platform
    // walkthrough works, this one does too.
    expect(ids(["self_pay", "platform"])).toEqual(ids(["self_pay"]))
  })

  it("adds the credentialing screens only when she asks for them", () => {
    const before = ids(["self_pay", "platform"])
    const after = ids(["self_pay", "platform"], true)

    expect(after).toEqual([...before.slice(0, -1), "payers", "record", "done"])
  })
})

describe("not seeing clients yet", () => {
  it("is a real answer, and walks the spine", () => {
    // An empty list is what that link means. It must never read back as "has
    // not answered", which is why null and [] are different things here.
    expect(ids([])).toEqual(["route", "plan", "confirm", "identity", "contact", "rates", "done"])
    expect(ids([])).not.toEqual(ids(null))
  })
})

describe("the options themselves", () => {
  it("names the platforms, because nobody says 'I'm on a platform'", () => {
    const platform = CURRENT_STATES.find((o) => o.id === "platform")
    expect(platform?.label).toContain("Headway")
    expect(platform?.label).toContain("Rula")
  })

  it("never implies leaving one", () => {
    // A therapist may intend to stay on a platform indefinitely, and that is a
    // perfectly good answer. The old fourth option assumed otherwise and shut
    // her out of the flow.
    for (const option of CURRENT_STATES) {
      const text = `${option.label} ${option.detail}`.toLowerCase()
      for (const word of ["leave", "leaving", "switch", "quit", "instead of"]) {
        expect(text).not.toContain(word)
      }
    }
  })

  it("says what superbills are for, where private pay lives", () => {
    // Out-of-network therapists are a large share of private pay and would not
    // otherwise know which line is theirs.
    const selfPay = CURRENT_STATES.find((o) => o.id === "self_pay")
    expect(selfPay?.detail).toContain("superbill")
  })
})

describe("taking card payments", () => {
  it("is absent until she asks for it", () => {
    // The ask lives on the plan screen, not in this list: it is about what she
    // WANTS, and nothing about how she is paid today implies it on its own.
    for (const state of everySubset()) {
      expect(ids(state)).not.toContain("payments")
    }
  })

  it("adds a step wherever she asks, whatever else is true", () => {
    // Same rule as every other tick on this wizard: an answer only ever ADDS.
    // A clinician on a platform who also wants to take card is an ordinary
    // practice, not a contradiction.
    for (const state of everySubset()) {
      expect(ids(state, false, true)).toContain("payments")
    }
  })

  it("collects the money question after the one about what a session is worth", () => {
    // How she collects follows what she charges. Asking first would be asking
    // her to set up a till before deciding what anything costs.
    const walked = ids(["self_pay"], false, true)
    expect(walked.indexOf("payments")).toBeGreaterThan(walked.indexOf("rates"))
  })

  it("leaves the credentialing branch where it was", () => {
    // The two wants are independent. Asking for one must not move or drop the
    // screens the other adds.
    const withBoth = ids(["own_insurance"], true, true)
    const withoutCard = ids(["own_insurance"], true, false)
    expect(withBoth.filter((id) => id !== "payments")).toEqual(withoutCard)
  })
})
