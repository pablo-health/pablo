// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Appointment type names are unique per clinician, and the server answers a
 * collision with 409. The card has to say so rather than drop the change
 * silently, and its own "Add a type" button must not walk into the
 * collision by creating a second "New type".
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { AppointmentTypesCard } from "../AppointmentTypesCard"
import { ApiError } from "@/lib/api/client"
import { renderWithProviders } from "@/test/renderWithProviders"

const mockTypes = vi.fn()
const mockPolicy = vi.fn()
const mockCreateType = vi.fn()
const mockUpdateType = vi.fn()

vi.mock("@/lib/api/appointmentTypes", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/appointmentTypes")>()),
  listAppointmentTypes: (...a: unknown[]) => mockTypes(...a),
  createAppointmentType: (...a: unknown[]) => mockCreateType(...a),
  updateAppointmentType: (...a: unknown[]) => mockUpdateType(...a),
}))

vi.mock("@/lib/api/schedulingPolicy", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/schedulingPolicy")>()),
  getSchedulingPolicy: (...a: unknown[]) => mockPolicy(...a),
}))

function type(id: string, name: string) {
  return {
    id,
    user_id: "user_1",
    name,
    default_fee_cents: null,
    duration_minutes: 50,
    cpt: null,
    audience: "existing",
    min_notice_hours: null,
    earliest_offer_business_days: 1,
    horizon: 10,
    horizon_unit: "business",
    self_bookable: false,
    offerable: true,
    created_at: null,
    updated_at: null,
  }
}

const NAME_TAKEN = "An appointment type with that name already exists."

describe("AppointmentTypesCard — names already in use", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockPolicy.mockResolvedValue({
      min_notice_hours: 24,
      max_horizon_days: 60,
      cancel_cutoff_hours: 24,
      reschedule_cutoff_hours: 24,
      pending_hold_hours: 72,
      self_book_existing: false,
      self_book_new: false,
      self_book_mode: "request",
      new_patient_flow: "consult",
      intake_forms_due_hours: 48,
    })
  })

  it("says so when a rename collides with another type", async () => {
    mockTypes.mockResolvedValue({ data: [type("t1", "Session"), type("t2", "Intake")], migrated: false })
    mockUpdateType.mockRejectedValue(new ApiError("CONFLICT", NAME_TAKEN, undefined, 409))
    const user = userEvent.setup()
    renderWithProviders(<AppointmentTypesCard />)

    await user.click((await screen.findAllByRole("button", { name: "Expand" }))[0])
    const name = screen.getByLabelText("Name")
    await user.clear(name)
    await user.type(name, "Intake")
    await user.tab()

    expect(await screen.findByRole("alert")).toHaveTextContent(NAME_TAKEN)
  })

  it("gives a new type a name that is not taken yet", async () => {
    mockTypes.mockResolvedValue({
      data: [type("t1", "New type"), type("t2", "New type 2")],
      migrated: false,
    })
    mockCreateType.mockResolvedValue(type("t3", "New type 3"))
    const user = userEvent.setup()
    renderWithProviders(<AppointmentTypesCard />)

    await screen.findByText("New type 2")
    await user.click(screen.getByRole("button", { name: "Add a type" }))

    await waitFor(() => expect(mockCreateType).toHaveBeenCalled())
    expect(mockCreateType.mock.calls[0][0]).toMatchObject({ name: "New type 3" })
    expect(screen.queryByRole("alert")).toBeNull()
  })
})
