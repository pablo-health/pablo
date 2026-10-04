// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the appointment types card shows, by where it is and whether clients
 * can book.
 *
 * The same records are edited in Settings > Scheduling and in the billing
 * setup wizard, and the two ask different questions of them:
 *
 *   * while the practice does not let clients book, the per-type Self-book
 *     switch and the patients' cancel/reschedule cutoffs are hidden — shown,
 *     they could only ever sit there disabled;
 *   * when Pablo may offer a type stays in Scheduling either way, because it
 *     is about Pablo's offers, not the client's booking;
 *   * the billing wizard shows what billing needs and nothing about offering
 *     or booking, whatever the policy says.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { AppointmentTypesCard } from "../AppointmentTypesCard"
import { renderWithProviders } from "@/test/renderWithProviders"
import { peopleWords, type PeopleTerm } from "@/lib/peopleTerm"

const peopleTerm = vi.hoisted(() => ({ current: "clients" as PeopleTerm }))

vi.mock("@/hooks/usePeopleTerm", async (orig) => ({
  ...(await orig<typeof import("@/hooks/usePeopleTerm")>()),
  usePeopleTerm: () => peopleWords(peopleTerm.current),
}))

const mockTypes = vi.fn()
const mockPolicy = vi.fn()
const mockUpdateType = vi.fn()

vi.mock("@/lib/api/appointmentTypes", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/appointmentTypes")>()),
  listAppointmentTypes: (...a: unknown[]) => mockTypes(...a),
  updateAppointmentType: (...a: unknown[]) => mockUpdateType(...a),
}))

vi.mock("@/lib/api/schedulingPolicy", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/schedulingPolicy")>()),
  getSchedulingPolicy: (...a: unknown[]) => mockPolicy(...a),
}))

const SESSION = {
  id: "type_session",
  user_id: "user_1",
  name: "Session",
  default_fee_cents: 16000,
  duration_minutes: 50,
  cpt: null,
  audience: "existing",
  min_notice_hours: null,
  earliest_offer_business_days: 1,
  horizon: 10,
  horizon_unit: "business",
  self_bookable: true,
  offerable: true,
  created_at: null,
  updated_at: null,
}

function policy(selfBook: boolean) {
  return {
    min_notice_hours: 24,
    max_horizon_days: 60,
    cancel_cutoff_hours: 24,
    reschedule_cutoff_hours: 24,
    pending_hold_hours: 72,
    self_book_existing: selfBook,
    self_book_new: false,
    self_book_mode: "request",
    new_patient_flow: "consult",
    intake_forms_due_hours: 48,
  }
}

describe("AppointmentTypesCard — booking controls", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockTypes.mockResolvedValue({ data: [SESSION], migrated: false })
  })

  it("hides the Self-book switch while clients cannot book, and keeps when Pablo may offer", async () => {
    mockPolicy.mockResolvedValue(policy(false))
    renderWithProviders(<AppointmentTypesCard />)

    expect(await screen.findByTestId("appointment-type-offering")).toHaveTextContent(
      "Offered from next day",
    )
    await waitFor(() => expect(mockPolicy).toHaveBeenCalled())
    expect(screen.queryByRole("switch", { name: "Session self-book" })).not.toBeInTheDocument()
  })

  it("shows the Self-book switch once clients can book, and it saves", async () => {
    mockPolicy.mockResolvedValue(policy(true))
    mockUpdateType.mockResolvedValue({ ...SESSION, self_bookable: false })
    renderWithProviders(<AppointmentTypesCard />)

    const toggle = await screen.findByRole("switch", { name: "Session self-book" })
    expect(toggle).toHaveAttribute("aria-checked", "true")
    await userEvent.click(toggle)
    expect(mockUpdateType).toHaveBeenCalledWith("type_session", { self_bookable: false }, undefined)
  })

  it("keeps the patients' cutoffs out of the defaults until they can book", async () => {
    mockPolicy.mockResolvedValue(policy(false))
    renderWithProviders(<AppointmentTypesCard />)

    await userEvent.click(await screen.findByRole("button", { name: /Defaults for all types/ }))

    expect(await screen.findByText("How much warning before any new booking")).toBeInTheDocument()
    expect(screen.queryByText("Clients may cancel until")).not.toBeInTheDocument()
    expect(screen.queryByText("Clients may reschedule until")).not.toBeInTheDocument()
  })

  it("shows the cutoffs once clients can book", async () => {
    mockPolicy.mockResolvedValue(policy(true))
    renderWithProviders(<AppointmentTypesCard />)

    await userEvent.click(await screen.findByRole("button", { name: /Defaults for all types/ }))

    expect(await screen.findByText("Clients may cancel until")).toBeInTheDocument()
    expect(screen.getByText("Clients may reschedule until")).toBeInTheDocument()
  })

  it("names the cutoffs with the clinician's own word", async () => {
    peopleTerm.current = "patients"
    try {
      mockPolicy.mockResolvedValue(policy(true))
      renderWithProviders(<AppointmentTypesCard />)

      await userEvent.click(await screen.findByRole("button", { name: /Defaults for all types/ }))

      expect(await screen.findByText("Patients may cancel until")).toBeInTheDocument()
      expect(screen.getByText("Patients may reschedule until")).toBeInTheDocument()
    } finally {
      peopleTerm.current = "clients"
    }
  })

  it("in the billing wizard, shows what billing needs and nothing about offering or booking", async () => {
    mockPolicy.mockResolvedValue(policy(true))
    renderWithProviders(<AppointmentTypesCard purpose="billing" />)

    expect(await screen.findByText("Session")).toBeInTheDocument()
    await waitFor(() => expect(mockPolicy).toHaveBeenCalled())
    expect(screen.queryByTestId("appointment-type-offering")).not.toBeInTheDocument()
    expect(screen.queryByRole("switch", { name: "Session self-book" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: /Defaults for all types/ })).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: "Expand" }))
    expect(screen.getByLabelText(/service code/i)).toBeInTheDocument()
    expect(screen.getByLabelText(/^fee/i)).toBeInTheDocument()
    expect(screen.queryByTestId("appointment-type-offering-fields")).not.toBeInTheDocument()
  })
})
