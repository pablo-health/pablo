// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { BookingLinkEditForm } from "../BookingLinkEditForm"
import { ApiError } from "@/lib/api/client"
import type { BookingLink } from "@/types/bookingLinks"

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

function makeLink(overrides: Partial<BookingLink> = {}): BookingLink {
  return {
    id: "link_1",
    slug: "intro-call",
    host_name: "Dr. Roe",
    title: "Intro call",
    description: null,
    duration_minutes: 30,
    session_type: "individual",
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

  it("cancels without calling the mutation", async () => {
    const onCancel = vi.fn()
    render(<BookingLinkEditForm link={makeLink()} onCancel={onCancel} onSaved={vi.fn()} />)
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Cancel" }))

    expect(onCancel).toHaveBeenCalled()
    expect(mutateUpdate).not.toHaveBeenCalled()
  })

  it("blocks submit on an out-of-range length", async () => {
    render(<BookingLinkEditForm link={makeLink()} onCancel={vi.fn()} onSaved={vi.fn()} />)
    const user = userEvent.setup()

    await user.clear(screen.getByLabelText("Length (minutes)"))
    await user.type(screen.getByLabelText("Length (minutes)"), "3")
    await user.click(screen.getByRole("button", { name: "Save" }))

    expect(mutateUpdate).not.toHaveBeenCalled()
    expect(
      screen.getByText("Length must be between 5 and 480 minutes.")
    ).toBeInTheDocument()
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
