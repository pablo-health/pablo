// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RecentlyDeletedPatients Component Tests (THERAPY-yg2)
 *
 * Covers the listing render, the days-remaining countdown wording, the
 * empty state, and the restore button -> API -> toast happy path.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { RecentlyDeletedPatients } from "../RecentlyDeletedPatients"
import * as patientsApi from "@/lib/api/patients"
import { ToastProvider } from "@/components/ui/Toast"
import { createMockPatient } from "@/test/factories"

vi.mock("@/lib/api/patients")

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
  Wrapper.displayName = "RecentlyDeletedWrapper"
  return { Wrapper, queryClient }
}

/**
 * Build a soft-deleted patient with a deletion stamp `daysAgo` days
 * before now and a matching `restore_deadline` 30 days after the
 * delete (the contract surfaced by the backend).
 */
function buildSoftDeleted(
  overrides: { id?: string; first_name?: string; last_name?: string; daysAgo?: number } = {}
) {
  const daysAgo = overrides.daysAgo ?? 5
  const deletedAt = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000)
  const restoreDeadline = new Date(deletedAt.getTime() + 30 * 24 * 60 * 60 * 1000)
  return createMockPatient({
    id: overrides.id ?? "patient-deleted-1",
    first_name: overrides.first_name ?? "Jane",
    last_name: overrides.last_name ?? "Doe",
    deleted_at: deletedAt.toISOString(),
    restore_deadline: restoreDeadline.toISOString(),
  })
}

describe("RecentlyDeletedPatients", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("renders the recently-deleted listing with countdown copy", async () => {
    const { Wrapper } = createWrapper()
    vi.mocked(patientsApi.listPatients).mockResolvedValue({
      data: [
        buildSoftDeleted({ id: "p1", first_name: "Jane", last_name: "Doe", daysAgo: 5 }),
      ],
      total: 1,
      page: 1,
      page_size: 1,
    })

    render(<RecentlyDeletedPatients />, { wrapper: Wrapper })

    await waitFor(() => {
      expect(screen.getByText("Jane Doe")).toBeInTheDocument()
    })
    // 30-day window minus 5 days elapsed = 25 days remaining (allow rounding).
    expect(screen.getByText(/2[45] days remaining/)).toBeInTheDocument()
  })

  it("calls listPatients with include_deleted=recent", async () => {
    const { Wrapper } = createWrapper()
    vi.mocked(patientsApi.listPatients).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      page_size: 0,
    })

    render(<RecentlyDeletedPatients />, { wrapper: Wrapper })

    await waitFor(() => {
      expect(patientsApi.listPatients).toHaveBeenCalledWith(
        { include_deleted: "recent" },
        undefined,
      )
    })
  })

  it("shows an empty-state message when there are no recently deleted patients", async () => {
    const { Wrapper } = createWrapper()
    vi.mocked(patientsApi.listPatients).mockResolvedValue({
      data: [],
      total: 0,
      page: 1,
      page_size: 0,
    })

    render(<RecentlyDeletedPatients />, { wrapper: Wrapper })

    await waitFor(() => {
      expect(
        screen.getByText(/no recently deleted patients/i),
      ).toBeInTheDocument()
    })
    // Per UX note from THERAPY-nyb the empty state explains the 30-day
    // retention without using the loaded "deleted forever" phrasing.
    expect(screen.getByText(/30 days/i)).toBeInTheDocument()
  })

  it("calls restorePatient on click and surfaces a success toast", async () => {
    const user = userEvent.setup()
    const { Wrapper } = createWrapper()
    const deleted = buildSoftDeleted({
      id: "p1",
      first_name: "Jane",
      last_name: "Doe",
      daysAgo: 1,
    })
    vi.mocked(patientsApi.listPatients).mockResolvedValue({
      data: [deleted],
      total: 1,
      page: 1,
      page_size: 1,
    })
    vi.mocked(patientsApi.restorePatient).mockResolvedValue({
      ...deleted,
      deleted_at: null,
      restore_deadline: null,
    })

    render(<RecentlyDeletedPatients />, { wrapper: Wrapper })

    await waitFor(() => {
      expect(screen.getByText("Jane Doe")).toBeInTheDocument()
    })

    const restoreButton = screen.getByRole("button", {
      name: /restore patient jane doe/i,
    })
    await user.click(restoreButton)

    await waitFor(() => {
      expect(patientsApi.restorePatient).toHaveBeenCalledWith("p1", undefined)
    })
    await waitFor(() => {
      expect(screen.getByText(/restored jane doe/i)).toBeInTheDocument()
    })
  })

  it("shows an error toast when the restore call fails", async () => {
    const user = userEvent.setup()
    const { Wrapper } = createWrapper()
    const deleted = buildSoftDeleted({ id: "p1", first_name: "Jane", last_name: "Doe" })
    vi.mocked(patientsApi.listPatients).mockResolvedValue({
      data: [deleted],
      total: 1,
      page: 1,
      page_size: 1,
    })
    vi.mocked(patientsApi.restorePatient).mockRejectedValue(new Error("boom"))

    render(<RecentlyDeletedPatients />, { wrapper: Wrapper })

    await waitFor(() => {
      expect(screen.getByText("Jane Doe")).toBeInTheDocument()
    })

    await user.click(
      screen.getByRole("button", { name: /restore patient jane doe/i }),
    )

    await waitFor(() => {
      expect(screen.getByText(/could not restore patient/i)).toBeInTheDocument()
    })
  })
})
