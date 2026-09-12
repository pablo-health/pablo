// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BookingLinkSettings } from "../BookingLinkSettings"
import { ApiError } from "@/lib/api/client"
import type { BookingLink } from "@/types/bookingLinks"
import type { AppointmentTypeResponse } from "@/types/scheduling"

const mockWriteText = vi.fn().mockResolvedValue(undefined)
Object.defineProperty(navigator, "clipboard", {
  value: { writeText: mockWriteText },
  writable: true,
  configurable: true,
})

const mutateCreate = vi.fn()
const mutateUpdate = vi.fn()
const mutateDelete = vi.fn()

let linksData: BookingLink[] = []
let listLoading = false
let listErrored = false
let createOnError: ((err: unknown) => void) | null = null
let typesData: AppointmentTypeResponse[] = []

vi.mock("@/hooks/useBookingLinks", () => ({
  useBookingLinks: () => ({
    data: { data: linksData, total: linksData.length },
    isLoading: listLoading,
    error: listErrored ? new Error("boom") : null,
  }),
  useCreateBookingLink: () => ({
    mutate: (data: unknown, opts?: { onSuccess?: () => void; onError?: (err: unknown) => void }) => {
      createOnError = opts?.onError ?? null
      mutateCreate(data)
    },
    isPending: false,
  }),
  useUpdateBookingLink: () => ({ mutate: mutateUpdate, isPending: false }),
  useDeleteBookingLink: () => ({ mutate: mutateDelete, isPending: false }),
}))

vi.mock("@/hooks/useAppointmentTypes", () => ({
  useAppointmentTypes: () => ({
    data: { data: typesData, total: typesData.length, migrated: false },
    isLoading: false,
    error: null,
  }),
}))

function makeType(overrides: Partial<AppointmentTypeResponse> = {}): AppointmentTypeResponse {
  return {
    id: "type_intake",
    user_id: "user_1",
    name: "Intake",
    default_fee_cents: null,
    duration_minutes: 30,
    cpt: null,
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

function renderWithClient() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <BookingLinkSettings />
    </QueryClientProvider>
  )
}

describe("BookingLinkSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    linksData = []
    listLoading = false
    listErrored = false
    createOnError = null
    typesData = [makeType()]
  })

  it("renders one row per link, named by its appointment type", () => {
    linksData = [
      makeLink(),
      makeLink({
        id: "link_2",
        slug: "follow-up",
        title: "Follow-up",
        appointment_type_id: "type_followup",
        appointment_type_name: "Follow-up session",
        duration_minutes: 60,
        is_active: false,
      }),
    ]
    renderWithClient()

    expect(screen.getByText("Intro call")).toBeInTheDocument()
    expect(screen.getByText("/book/intro-call")).toBeInTheDocument()
    expect(screen.getByText("30 min · Intake")).toBeInTheDocument()
    expect(screen.getByText("60 min · Follow-up session")).toBeInTheDocument()
    expect(screen.getByText("Active")).toBeInTheDocument()
    expect(screen.getByText("Inactive")).toBeInTheDocument()
  })

  it("tells the owner why a link cannot take bookings", () => {
    linksData = [
      makeLink({
        bookable: false,
        not_bookable_reason: "This appointment type is not marked as bookable by clients.",
      }),
    ]
    renderWithClient()

    expect(screen.getByRole("note")).toHaveTextContent(
      "Not bookable yet: This appointment type is not marked as bookable by clients."
    )
  })

  it("says so when the link's appointment type has been deleted", () => {
    linksData = [
      makeLink({
        appointment_type_name: null,
        duration_minutes: null,
        bookable: false,
        not_bookable_reason: "The appointment type this link books no longer exists.",
      }),
    ]
    renderWithClient()

    expect(screen.getByText("Appointment type deleted")).toBeInTheDocument()
  })

  it("shows an empty state and a loading skeleton", () => {
    const { rerender } = renderWithClient()
    expect(screen.getByText("No booking links yet.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "New booking link" })).toBeInTheDocument()

    listLoading = true
    rerender(
      <QueryClientProvider client={new QueryClient()}>
        <BookingLinkSettings />
      </QueryClientProvider>
    )
    expect(screen.getByRole("status")).toBeInTheDocument()
  })

  it("shows an inline error when the list fails to load", () => {
    listErrored = true
    renderWithClient()
    expect(screen.getByText("Couldn't load your booking links.")).toBeInTheDocument()
  })

  it("copies the shareable URL and flips the button text", async () => {
    linksData = [makeLink()]
    renderWithClient()

    fireEvent.click(screen.getByRole("button", { name: "Copy link" }))

    expect(mockWriteText).toHaveBeenCalledWith(`${window.location.origin}/book/intro-call`)
    expect(await screen.findByRole("button", { name: "Copied" })).toBeInTheDocument()
  })

  it("deactivates an active link", async () => {
    linksData = [makeLink()]
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Deactivate" }))

    expect(mutateUpdate).toHaveBeenCalledWith({ linkId: "link_1", data: { is_active: false } })
  })

  it("activates an inactive link", async () => {
    linksData = [makeLink({ is_active: false })]
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Activate" }))

    expect(mutateUpdate).toHaveBeenCalledWith({ linkId: "link_1", data: { is_active: true } })
  })

  it("reveals the create form and blocks submit on a bad slug", async () => {
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "New booking link" }))
    await user.type(screen.getByLabelText("Slug"), "Bad Slug")
    await user.type(screen.getByLabelText("Host name"), "Dr. Roe")
    await user.type(screen.getByLabelText("Title"), "Intro call")
    await user.click(screen.getByRole("button", { name: "Create link" }))

    expect(mutateCreate).not.toHaveBeenCalled()
    expect(
      screen.getByText("Slug must be 3–64 lowercase letters, numbers or dashes.")
    ).toBeInTheDocument()
  })

  it("normalises slug casing as typed and previews the URL path", async () => {
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "New booking link" }))
    await user.type(screen.getByLabelText("Slug"), "Intro-Call")

    expect(screen.getByLabelText("Slug")).toHaveValue("intro-call")
    expect(screen.getByText("/book/intro-call")).toBeInTheDocument()
  })

  it("submits a valid create request booking the first appointment type", async () => {
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "New booking link" }))
    await user.type(screen.getByLabelText("Slug"), "Intro-Call")
    await user.type(screen.getByLabelText("Host name"), "Dr. Roe")
    await user.type(screen.getByLabelText("Title"), "Intro call")
    await user.click(screen.getByRole("button", { name: "Create link" }))

    expect(mutateCreate).toHaveBeenCalledWith({
      slug: "intro-call",
      host_name: "Dr. Roe",
      title: "Intro call",
      appointment_type_id: "type_intake",
    })
  })

  it("cannot create a link until the practice has an appointment type", async () => {
    typesData = []
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "New booking link" }))

    expect(
      screen.getByText(
        "Create an appointment type under Scheduling first. A booking link books one type."
      )
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Create link" })).toBeDisabled()
  })

  it("surfaces the server's own message on a 409 slug conflict", async () => {
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "New booking link" }))
    await user.type(screen.getByLabelText("Slug"), "intro-call")
    await user.type(screen.getByLabelText("Host name"), "Dr. Roe")
    await user.type(screen.getByLabelText("Title"), "Intro call")
    await user.click(screen.getByRole("button", { name: "Create link" }))

    const message =
      "This slug is taken. Slugs stay reserved after a link is deleted, so pick another."
    createOnError?.(new ApiError("CONFLICT", message, undefined, 409))

    expect(await screen.findByRole("alert")).toHaveTextContent(message)
  })

  it("surfaces the server's own message on a 400 reserved slug", async () => {
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "New booking link" }))
    await user.type(screen.getByLabelText("Slug"), "intro-call")
    await user.type(screen.getByLabelText("Host name"), "Dr. Roe")
    await user.type(screen.getByLabelText("Title"), "Intro call")
    await user.click(screen.getByRole("button", { name: "Create link" }))

    const message = "This slug is reserved. Please choose another."
    createOnError?.(new ApiError("BAD_REQUEST", message, undefined, 400))

    expect(await screen.findByRole("alert")).toHaveTextContent(message)
  })

  it("swaps a row for the edit form when Edit is clicked", async () => {
    linksData = [makeLink()]
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Edit" }))

    expect(screen.getByLabelText("Host name")).toHaveValue("Dr. Roe")
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument()
  })

  it("opens the delete dialog when Delete is clicked", async () => {
    linksData = [makeLink()]
    renderWithClient()
    const user = userEvent.setup()

    await user.click(screen.getByRole("button", { name: "Delete" }))

    const dialog = await screen.findByRole("dialog")
    expect(dialog).toHaveTextContent("Delete this booking link?")
  })
})
