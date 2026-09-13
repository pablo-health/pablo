// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What each route actually walks, and in what order.
 *
 * Order is a design decision here, not a detail. The NPI lookup returns her
 * legal name, credential, taxonomy, licence and practice address — most of
 * what the practice steps ask her to type. It sat after them, which meant
 * asking her to type what we were one click from knowing.
 *
 * Bug classes covered:
 *   * the lookup drifting back behind the practice steps, which quietly turns
 *     three confirmations into three forms again;
 *   * a route losing the lookup. Every route needs it, including private pay:
 *     superbill.py requires the rendering provider's NPI, so a clinician who
 *     bills nobody still cannot hand her client a superbill without one;
 *   * a route losing its ending, so a branch stops rather than finishing;
 *   * the stepper revealing the whole journey before she has chosen, which
 *     makes the choice feel like it cost her something.
 */

import { describe, expect, it } from "vitest"
import { PAYMENT_ROUTES, stepsForRoute, type PaymentRouteId } from "../routes"

const ids = (route: PaymentRouteId | null) => stepsForRoute(route).map((s) => s.id)

const ALL_ROUTES = PAYMENT_ROUTES.map((r) => r.id)

describe("before she has answered", () => {
  it("shows only the spine", () => {
    expect(ids(null)).toEqual(["route", "identity", "contact", "rates"])
  })

  it("does not reveal the credentialing steps", () => {
    expect(ids(null)).not.toContain("confirm")
    expect(ids(null)).not.toContain("payers")
  })
})

describe("every route looks her up first", () => {
  it.each(ALL_ROUTES)("%s asks for the NPI before the practice steps", (route) => {
    const order = ids(route)

    expect(order).toContain("confirm")
    expect(order.indexOf("confirm")).toBeLessThan(order.indexOf("identity"))
    expect(order.indexOf("confirm")).toBeLessThan(order.indexOf("contact"))
    expect(order.indexOf("confirm")).toBeLessThan(order.indexOf("rates"))
  })

  it.each(ALL_ROUTES)("%s still asks the routing question first", (route) => {
    expect(ids(route)[0]).toBe("route")
  })

  it("includes private pay, because a superbill carries her NPI", () => {
    // superbill.py lists npi in _RENDERING_PROVIDER_REQUIRED: without one we
    // cannot produce a superbill at all. A clinician who bills nobody still
    // hands her client a document that carries it, and the moment she finds
    // that out must not be the moment a client asks for her paperwork.
    expect(ids("private_pay")).toEqual(["route", "confirm", "identity", "contact", "rates", "done"])
  })

  it("includes the clinician who is already paneled", () => {
    // She files claims herself, and her individual NPI is the rendering
    // provider on every one of them. This route had no NPI step at all.
    expect(ids("already_paneled")).toEqual([
      "route",
      "confirm",
      "identity",
      "contact",
      "rates",
      "payers",
      "done",
    ])
  })

  it.each(["wants_panels", "platform_to_own"] as PaymentRouteId[])(
    "%s walks the whole journey",
    (route) => {
      expect(ids(route)).toEqual([
        "route",
        "confirm",
        "identity",
        "contact",
        "rates",
        "payers",
        "record",
        "done",
      ])
    },
  )
})

describe("every route", () => {
  it.each(ALL_ROUTES)("%s ends somewhere rather than stopping", (route) => {
    expect(ids(route).at(-1)).toBe("done")
  })

  it.each(ALL_ROUTES)("%s collects the practice details", (route) => {
    // The shared spine: a superbill and a claim need the same facts about who
    // she is and what she charges.
    expect(ids(route)).toEqual(expect.arrayContaining(["identity", "contact", "rates"]))
  })

  it.each(ALL_ROUTES)("%s names each step once", (route) => {
    const order = ids(route)
    expect(new Set(order).size).toBe(order.length)
  })
})
