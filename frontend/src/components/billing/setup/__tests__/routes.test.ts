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
 *   * private pay growing a credentialing step, which would ask a cash-only
 *     therapist for a number she may not have and does not need;
 *   * a route losing its ending, so a branch stops rather than finishing;
 *   * the stepper revealing the whole journey before she has chosen, which
 *     makes the choice feel like it cost her something.
 */

import { describe, expect, it } from "vitest"
import { PAYMENT_ROUTES, stepsForRoute, type PaymentRouteId } from "../routes"

const ids = (route: PaymentRouteId | null) => stepsForRoute(route).map((s) => s.id)

const CREDENTIALING_ROUTES: PaymentRouteId[] = ["wants_panels", "platform_to_own"]

describe("before she has answered", () => {
  it("shows only the spine", () => {
    expect(ids(null)).toEqual(["route", "identity", "contact", "rates"])
  })

  it("does not reveal the credentialing steps", () => {
    expect(ids(null)).not.toContain("confirm")
    expect(ids(null)).not.toContain("payers")
  })
})

describe("the credentialing routes look her up first", () => {
  it.each(CREDENTIALING_ROUTES)("%s asks for the NPI before the practice steps", (route) => {
    const order = ids(route)

    expect(order.indexOf("confirm")).toBeLessThan(order.indexOf("identity"))
    expect(order.indexOf("confirm")).toBeLessThan(order.indexOf("contact"))
    expect(order.indexOf("confirm")).toBeLessThan(order.indexOf("rates"))
  })

  it.each(CREDENTIALING_ROUTES)("%s still asks the routing question first", (route) => {
    expect(ids(route)[0]).toBe("route")
  })

  it.each(CREDENTIALING_ROUTES)("%s walks the whole journey", (route) => {
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
  })
})

describe("the routes that are not being credentialed", () => {
  it("private pay never meets the registry", () => {
    // She is not applying to anybody. A lookup here would be a question about
    // a number she may not have and does not need.
    expect(ids("private_pay")).not.toContain("confirm")
    expect(ids("private_pay")).toEqual(["route", "identity", "contact", "rates", "done"])
  })

  it("already paneled is asked about payers, not about credentialing", () => {
    expect(ids("already_paneled")).toEqual([
      "route",
      "identity",
      "contact",
      "rates",
      "payers",
      "done",
    ])
  })
})

describe("every route", () => {
  it.each(PAYMENT_ROUTES.map((r) => r.id))("%s ends somewhere rather than stopping", (route) => {
    expect(ids(route).at(-1)).toBe("done")
  })

  it.each(PAYMENT_ROUTES.map((r) => r.id))("%s collects the practice details", (route) => {
    // The shared spine: a superbill and a claim need the same facts about who
    // she is and what she charges.
    expect(ids(route)).toEqual(expect.arrayContaining(["identity", "contact", "rates"]))
  })

  it.each(PAYMENT_ROUTES.map((r) => r.id))("%s names each step once", (route) => {
    const order = ids(route)
    expect(new Set(order).size).toBe(order.length)
  })
})
