// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The booking-link list against the real query hooks, with only sign-in and
 * the network stubbed.
 *
 * The sibling test mocks useBookingLinks outright, so it cannot see what the
 * query does while sign-in is still resolving: the query is disabled, which
 * leaves it pending but not fetching. The page used to treat that state as
 * loaded, show "New booking link", and then unmount the open form the moment
 * sign-in resolved and the fetch began, wiping what had been typed.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { BookingLinkSettings } from "../BookingLinkSettings"
import { queryKeys } from "@/lib/api/queryKeys"

let authLoading = true

vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: null, loading: authLoading, getIdToken: async () => null }),
}))

const listBookingLinks = vi.fn()

vi.mock("@/lib/api/bookingLinks", () => ({
  listBookingLinks: (...args: unknown[]) => listBookingLinks(...args),
  createBookingLink: vi.fn(),
  updateBookingLink: vi.fn(),
  deleteBookingLink: vi.fn(),
}))

vi.mock("@/hooks/useAppointmentTypes", () => ({
  useAppointmentTypes: () => ({
    data: {
      data: [
        {
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
        },
      ],
      total: 1,
      migrated: false,
    },
  }),
}))

function renderPage(queryClient: QueryClient) {
  const tree = () => (
    <QueryClientProvider client={queryClient}>
      <BookingLinkSettings />
    </QueryClientProvider>
  )
  const view = render(tree())
  return { rerenderPage: () => view.rerender(tree()) }
}

describe("BookingLinkSettings while sign-in resolves", () => {
  beforeEach(() => {
    authLoading = true
    listBookingLinks.mockReset()
    listBookingLinks.mockResolvedValue({ data: [], total: 0 })
  })

  it("offers no create form until the list has actually loaded", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const { rerenderPage } = renderPage(queryClient)

    expect(screen.getByRole("status")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "New booking link" })).toBeNull()
    expect(screen.queryByText("No booking links yet.")).toBeNull()
    expect(listBookingLinks).not.toHaveBeenCalled()

    authLoading = false
    rerenderPage()

    expect(await screen.findByRole("button", { name: "New booking link" })).toBeInTheDocument()
    expect(screen.getByText("No booking links yet.")).toBeInTheDocument()
  })

  it("keeps what was typed into an open form while the list loads again from scratch", async () => {
    authLoading = false
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    renderPage(queryClient)
    const user = userEvent.setup()

    await user.click(await screen.findByRole("button", { name: "New booking link" }))
    await user.type(screen.getByLabelText("Slug"), "intro-call")

    let resolveRefetch: (v: unknown) => void = () => {}
    listBookingLinks.mockReturnValueOnce(new Promise((resolve) => (resolveRefetch = resolve)))
    // A reset drops the cached list, so the query is pending AND fetching:
    // the state the old early-return skeleton unmounted the form in.
    await act(async () => {
      void queryClient.resetQueries({ queryKey: queryKeys.bookingLinks.all })
    })
    // react-query delivers the state change on a timer, so wait for it.
    expect(await screen.findByRole("status")).toBeInTheDocument()
    expect(screen.getByLabelText("Slug")).toHaveValue("intro-call")

    await act(async () => resolveRefetch({ data: [], total: 0 }))
    expect(screen.getByLabelText("Slug")).toHaveValue("intro-call")
  })
})
