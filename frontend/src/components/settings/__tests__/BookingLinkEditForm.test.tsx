// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { BookingLinkEditForm } from "../BookingLinkEditForm"
import { ApiError } from "@/lib/api/client"
import type { BookingLink } from "@/types/bookingLinks"
import type { AppointmentTypeResponse } from "@/types/scheduling"

const mutateUpdate = vi.fn()
let updateOnError: ((err: unknown) => void) | null = null

vi.mock("@/hooks/useBookingLinks", () => ({
  useUpdateBookingLink: () => ({
    mutate: (
      vars: unknown,
      opts?: { onSuccess?: () => void; onError?: (err: unknown) => void }
    ) => {
      updateOnError = opts?.onError ?? null
      mutateUpdate(vars)
    },
    isPending: false,
  }),
}))

function makeType(overrides: Partial<AppointmentTypeResponse> = {}): AppointmentTypeResponse {
  return {
    id: "type_intake",
    user_id: "user_1",
    name: "Intake",
    default_fee_cents: null,
    duration_minutes: 30,
    audience: "new",
    min_notice_hours: null,
    earliest_offer_business_days: 1,
    horizon: 10,
    horizon_unit: "business",
    self_bookable: true,
    offerable: true,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

vi.mock("@/hooks/useAppointmentTypes", () => ({
  useAppointmentTypes: () => ({
    data: {
      data: [makeType(), makeType({ id: "type_followup", name: "Follow-up", duration_minutes: 50 })],
      total: 2,
      migrated: false,
    },
    isLoading: false,
    error: null,
  }),
}))

function makeLink(overrides: Partial<BookingLink> = {}): BookingLink {
  return {
    id: "link_1",
    slug: "intro-call",
    host_name: "Dr. Roe",
    title: "Intro call",
    description: null,
    appointment_type_id: "type_intake",
    appointment_type_name: "Intake",
    duration_minutes: 30,
    bookable: true,
    not_bookable_reason: null,
    is_active: true,
    created_at: "2026-08-01T00:00:00Z",
    updated_at: "2026-08-01T00:00:00Z",
    ...overrides,
  }
}

describe("BookingLinkEditForm", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    updateOnError = null
  })

  it("renders the slug as read-only text with the immutability helper", () => {
    render(<BookingLinkEditForm link={makeLink()} onCancel={vi.fn()} onSaved={vi.fn()} />)

    expect(screen.getByText("/book/intro-call")).toBeInTheDocument()
    expect(screen.queryByLabelText("Slug")).not.toBeInTheDocument()
    expect(
      screen.getByText(
        "Slugs can't be changed. Deactivate this link and create a new one if you need a different address."
      )
    ).toBeInTheDocument()
  })

  it("saves only the changed field", async () => {
    render(<BookingLinkEditForm link={makeLink()} onCancel={vi.fn()} onSaved={vi.fn()} />)
    const user = userEvent.setup()

    await user.clear(screen.getByLabelText("Title"))
    await user.type(screen.getByLabelText("Title"), "Consultation")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mutateUpdate).toHaveBeenCalledWith({
      linkId: "link_1",
      data: { title: "Consultation" },
    })
  })

  it("shows the current appointment type and offers no length field", () => {
    render(<BookingLinkEditForm link={makeLink()} onCancel={vi.fn()} onSaved={vi.fn()} />)

    expect(screen.getByLabelText("Appointment type")).toHaveTextContent("Intake · 30 min")
    expect(screen.queryByLabelText("Length (minutes)")).not.toBeInTheDocument()
  })

  it("cancels without calling the mutation", async () => {
    const onCancel = vi.fn()
    render(<BookingLinkEditForm link={makeLink()} onCancel={onCancel} onSaved={vi.fn()} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Cancel" }))

    expect(onCancel).toHaveBeenCalled()
    expect(mutateUpdate).not.toHaveBeenCalled()
  })

  it("renders a server error from the mutation in a role=alert element", async () => {
    render(<BookingLinkEditForm link={makeLink()} onCancel={vi.fn()} onSaved={vi.fn()} />)
    const user = userEvent.setup()

    await user.clear(screen.getByLabelText("Title"))
    await user.type(screen.getByLabelText("Title"), "Consultation")
    await user.click(screen.getByRole("button", { name: "Save" }))

    const message = "Booking link not found"
    updateOnError?.(new ApiError("NOT_FOUND", message, undefined, 404))

    expect(await screen.findByRole("alert")).toHaveTextContent(message)
  })
})
