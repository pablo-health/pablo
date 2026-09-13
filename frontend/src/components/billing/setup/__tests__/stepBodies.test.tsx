// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every step a route can reach has a real screen behind it.
 *
 * The type system already guarantees a body EXISTS for each `StepId` — the
 * table is `Record<StepId, ...>`. What it cannot say is whether that body is a
 * screen or a placeholder, and for months two routes compiled perfectly while
 * dead-ending on "This step isn't built yet": the record step, and then again
 * at their ending.
 *
 * So this walks the ids each route actually reaches and asserts none of them
 * renders the not-built panel. A future step added as a stub is then a failing
 * test rather than something a therapist finds by walking the flow.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"

import { STEP_BODIES } from "../stepBodies"
import { PAYMENT_ROUTES, stepsForRoute, type PaymentRouteId } from "../routes"

vi.mock("@/components/settings/PracticeIdentityCard", () => ({
  PracticeIdentityCard: () => <div />,
}))
vi.mock("@/components/settings/BillingContactCard", () => ({
  BillingContactCard: () => <div />,
}))
vi.mock("@/components/settings/AppointmentTypesCard", () => ({
  AppointmentTypesCard: () => <div />,
}))
vi.mock("@/components/settings/PayersCard", () => ({ PayersCard: () => <div /> }))
vi.mock("@/components/credentialing/CredentialingWizard", () => ({
  CredentialingWizard: () => <div>credentialing checklist</div>,
}))
vi.mock("@/components/credentialing/NpiLookupStep", () => ({
  NpiLookupStep: () => <div />,
}))
vi.mock("@/hooks/useBillingProfile", () => ({
  useBillingProfile: () => ({ data: undefined, isLoading: false }),
}))

const NOT_BUILT = /isn.t built yet/i

describe("every route's steps have a real screen", () => {
  it.each(PAYMENT_ROUTES.map((r) => [r.id, r.label] as [PaymentRouteId, string]))(
    "%s renders no placeholder on any step",
    (routeId) => {
      for (const step of stepsForRoute(routeId)) {
        const Body = STEP_BODIES[step.id]
        const { unmount } = render(
          <Body route={routeId} onChoose={() => {}} />,
        )
        expect(
          screen.queryByText(NOT_BUILT),
          `${routeId} → ${step.id} is still a placeholder`,
        ).not.toBeInTheDocument()
        unmount()
      }
    },
  )

  it("shows the credentialing checklist on the record step", () => {
    // Mounted, not rebuilt — the same component Settings uses, so the two
    // cannot drift into disagreeing about one record.
    const Body = STEP_BODIES.record
    render(<Body route="wants_panels" onChoose={() => {}} />)

    expect(screen.getByText("credentialing checklist")).toBeInTheDocument()
  })

  it("does not tell an unpanelled clinician she is set up to bill", () => {
    // The already-paneled ending says exactly that, and it would be a lie
    // here: she has recorded her facts and applied to nobody. Saying it would
    // send her looking for claims that cannot exist yet.
    const Body = STEP_BODIES.done
    render(<Body route="wants_panels" onChoose={() => {}} />)

    expect(screen.queryByText(/set up to bill/i)).not.toBeInTheDocument()
    expect(screen.getByText(/superbill/i)).toBeInTheDocument()
  })
})
