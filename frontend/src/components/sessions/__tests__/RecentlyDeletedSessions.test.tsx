// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecentlyDeletedSessions tests (PABLO-ocvu)
 *
 * Covers the per-chart query, the listing with its countdown, the
 * nothing-to-undo case (no section at all), and the Restore button ->
 * API -> toast path, including the client-still-deleted refusal.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RecentlyDeletedSessions } from "../RecentlyDeletedSessions"
import * as sessionsApi from "@/lib/api/sessions"
import { ApiError } from "@/lib/api/client"
import { ToastProvider } from "@/components/ui/Toast"
import { createMockSession } from "@/test/factories"

vi.mock("@/lib/api/sessions")
vi.mock("@/lib/auth-context", () => ({
  useAuth: () => ({ user: null, loading: false, getIdToken: async () => null }),
}))

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  )
  Wrapper.displayName = "RecentlyDeletedSessionsWrapper"
  return Wrapper
}

/** A session deleted `daysAgo` days ago, with the backend's 30-day deadline. */
function buildSoftDeleted(overrides: { id?: string; session_number?: number; daysAgo?: number } = {}) {
  const daysAgo = overrides.daysAgo ?? 5
  const deletedAt = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000)
  const restoreDeadline = new Date(deletedAt.getTime() + 30 * 24 * 60 * 60 * 1000)
  return createMockSession({
    id: overrides.id ?? "session-deleted-1",
    patient_id: "patient-456",
    session_number: overrides.session_number ?? 3,
    session_date: "2026-02-04T15:00:00Z",
    deleted_at: deletedAt.toISOString(),
    restore_deadline: restoreDeadline.toISOString(),
  })
}

const emptyList = { data: [], total: 0, page: 1, page_size: 0 }

describe("RecentlyDeletedSessions", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("asks for the chart's recently deleted sessions", async () => {
    vi.mocked(sessionsApi.listSessions).mockResolvedValue(emptyList)

    render(<RecentlyDeletedSessions patientId="patient-456" />, { wrapper: createWrapper() })

    await waitFor(() => {
      expect(sessionsApi.listSessions).toHaveBeenCalledWith(
        { patient_id: "patient-456", include_deleted: "recent" },
        undefined,
      )
    })
  })

  it("renders nothing when there is nothing to undo", async () => {
    vi.mocked(sessionsApi.listSessions).mockResolvedValue(emptyList)

    const { container } = render(<RecentlyDeletedSessions patientId="patient-456" />, {
      wrapper: createWrapper(),
    })

    await waitFor(() => expect(sessionsApi.listSessions).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
    expect(screen.queryByText("Recently deleted")).not.toBeInTheDocument()
  })

  it("lists each deleted session with its number and days remaining", async () => {
    vi.mocked(sessionsApi.listSessions).mockResolvedValue({
      data: [buildSoftDeleted({ id: "s1", session_number: 3, daysAgo: 5 })],
      total: 1,
      page: 1,
      page_size: 1,
    })

    render(<RecentlyDeletedSessions patientId="patient-456" />, { wrapper: createWrapper() })

    expect(await screen.findByText("Recently deleted")).toBeInTheDocument()
    expect(screen.getByText("Session 3")).toBeInTheDocument()
    // 30-day window minus 5 days elapsed = 25 days remaining (allow rounding).
    expect(screen.getByText(/2[45] days remaining/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Restore session 3" })).toBeInTheDocument()
  })

  it("restores on click and confirms in a toast", async () => {
    const deleted = buildSoftDeleted({ id: "s1", session_number: 3 })
    vi.mocked(sessionsApi.listSessions).mockResolvedValue({
      data: [deleted],
      total: 1,
      page: 1,
      page_size: 1,
    })
    vi.mocked(sessionsApi.restoreSession).mockResolvedValue(
      createMockSession({ id: "s1", session_number: 3, patient_id: "patient-456" }),
    )

    render(<RecentlyDeletedSessions patientId="patient-456" />, { wrapper: createWrapper() })

    await userEvent.click(await screen.findByRole("button", { name: "Restore session 3" }))

    await waitFor(() => {
      expect(sessionsApi.restoreSession).toHaveBeenCalledWith("s1", undefined)
    })
    expect(await screen.findByText("Restored session 3.")).toBeInTheDocument()
  })

  it("points at the client when the session went with a deleted client", async () => {
    vi.mocked(sessionsApi.listSessions).mockResolvedValue({
      data: [buildSoftDeleted({ id: "s1", session_number: 3 })],
      total: 1,
      page: 1,
      page_size: 1,
    })
    vi.mocked(sessionsApi.restoreSession).mockRejectedValue(
      new ApiError("PATIENT_DELETED", "Restore the client first"),
    )

    render(<RecentlyDeletedSessions patientId="patient-456" />, { wrapper: createWrapper() })

    await userEvent.click(await screen.findByRole("button", { name: "Restore session 3" }))

    expect(
      await screen.findByText(/Restore the client first; this session comes back with them\./),
    ).toBeInTheDocument()
  })
})
