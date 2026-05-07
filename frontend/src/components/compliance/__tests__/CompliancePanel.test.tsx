// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { CompliancePanel } from "../CompliancePanel"
import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"

const useComplianceItems = vi.hoisted(() => vi.fn())
const useComplianceTemplates = vi.hoisted(() => vi.fn())
const useCompleteComplianceItem = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useCompliance", () => ({
  useComplianceItems: (...args: unknown[]) => useComplianceItems(...args),
  useComplianceTemplates: (...args: unknown[]) => useComplianceTemplates(...args),
  useCompleteComplianceItem: (...args: unknown[]) =>
    useCompleteComplianceItem(...args),
  useCreateComplianceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateComplianceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

const LICENSE_TEMPLATE: ComplianceTemplate = {
  item_type: "license",
  label: "Professional license",
  description: "",
  cadence_days: null,
  reminder_windows: [90, 60, 30, 0],
  multi_instance: false,
  min_edition: "core",
  sort_order: 10,
}

function makeItem(overrides: Partial<ComplianceItem> = {}): ComplianceItem {
  return {
    id: "item-1",
    item_type: "license",
    label: "Professional license",
    due_date: null,
    notes: null,
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

function renderPanel() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <CompliancePanel />
    </QueryClientProvider>,
  )
}

describe("CompliancePanel", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-05-07T12:00:00Z"))
    useCompleteComplianceItem.mockReturnValue({
      mutate: vi.fn(),
      isPending: false,
    })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("shows the onboarding empty state when no items are saved", () => {
    useComplianceItems.mockReturnValue({ data: [], isLoading: false })
    useComplianceTemplates.mockReturnValue({ data: [LICENSE_TEMPLATE] })

    renderPanel()

    expect(screen.getByText(/let's set up your reminders/i)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: /start setup/i })).toBeInTheDocument()
  })

  it("shows the all-clear state when items are saved but none urgent", () => {
    useComplianceItems.mockReturnValue({
      data: [makeItem({ due_date: "2027-12-01" })], // far future
      isLoading: false,
    })
    useComplianceTemplates.mockReturnValue({ data: [LICENSE_TEMPLATE] })

    renderPanel()

    expect(
      screen.getByText(/you'?re all caught up\. pablo'?s got it from here\./i),
    ).toBeInTheDocument()
    expect(
      screen.getByRole("button", { name: /manage/i }),
    ).toBeInTheDocument()
  })

  it("surfaces overdue and due-soon items, sorted by urgency", () => {
    useComplianceItems.mockReturnValue({
      data: [
        makeItem({ id: "soon", due_date: "2026-06-01" }), // ~25 days
        makeItem({ id: "overdue", due_date: "2026-04-15" }), // overdue
        makeItem({ id: "later", due_date: "2027-01-01" }), // upcoming, hidden
      ],
      isLoading: false,
    })
    useComplianceTemplates.mockReturnValue({ data: [LICENSE_TEMPLATE] })

    renderPanel()

    const items = screen.getAllByRole("listitem")
    expect(items).toHaveLength(2)
    expect(items[0]).toHaveTextContent(/overdue/i)
    expect(items[1]).toHaveTextContent(/due in 25 days/i)
  })

  it("calls completeItem.mutate when 'Mark done' is clicked", async () => {
    const mutate = vi.fn()
    useCompleteComplianceItem.mockReturnValue({ mutate, isPending: false })
    useComplianceItems.mockReturnValue({
      data: [makeItem({ id: "soon", due_date: "2026-06-01" })],
      isLoading: false,
    })
    useComplianceTemplates.mockReturnValue({ data: [LICENSE_TEMPLATE] })

    renderPanel()

    const button = screen.getByRole("button", { name: /mark done/i })
    button.click()

    expect(mutate).toHaveBeenCalledWith("soon")
  })
})
