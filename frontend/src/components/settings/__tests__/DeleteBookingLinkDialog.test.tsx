// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { DeleteBookingLinkDialog } from "../DeleteBookingLinkDialog"
import type { BookingLink } from "@/types/bookingLinks"

const mutateDelete = vi.fn()
const mutateUpdate = vi.fn()

vi.mock("@/hooks/useBookingLinks", () => ({
  useDeleteBookingLink: () => ({
    mutate: (linkId: unknown, opts?: { onSuccess?: () => void }) => {
      mutateDelete(linkId)
      opts?.onSuccess?.()
    },
    isPending: false,
  }),
  useUpdateBookingLink: () => ({
    mutate: (vars: unknown, opts?: { onSuccess?: () => void }) => {
      mutateUpdate(vars)
      opts?.onSuccess?.()
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

describe("DeleteBookingLinkDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("describes the tombstone behavior in the dialog body", () => {
    render(
      <DeleteBookingLinkDialog link={makeLink()} open onOpenChange={vi.fn()} />
    )

    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveTextContent("/book/intro-call")
    expect(dialog).toHaveTextContent("stays reserved")
  })

  it("deletes the link and calls neither on Cancel", async () => {
    const onOpenChange = vi.fn()
    render(
      <DeleteBookingLinkDialog link={makeLink()} open onOpenChange={onOpenChange} />
    )
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Delete link" }))

    expect(mutateDelete).toHaveBeenCalledWith("link_1")
    expect(mutateUpdate).not.toHaveBeenCalled()
  })

  it("deactivates instead of deleting", async () => {
    const onOpenChange = vi.fn()
    render(
      <DeleteBookingLinkDialog link={makeLink()} open onOpenChange={onOpenChange} />
    )
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deactivate instead" }))

    expect(mutateUpdate).toHaveBeenCalledWith({
      linkId: "link_1",
      data: { is_active: false },
    })
    expect(mutateDelete).not.toHaveBeenCalled()
  })

  it("calls neither mutation on Cancel", async () => {
    const onOpenChange = vi.fn()
    render(
      <DeleteBookingLinkDialog link={makeLink()} open onOpenChange={onOpenChange} />
    )
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Cancel" }))

    expect(mutateDelete).not.toHaveBeenCalled()
    expect(mutateUpdate).not.toHaveBeenCalled()
    expect(onOpenChange).toHaveBeenCalledWith(false)
  })
})
