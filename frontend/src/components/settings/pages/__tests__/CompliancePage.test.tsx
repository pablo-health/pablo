// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice > Compliance — the panel on a page of its own, and the link that
 * opens it on one item.
 *
 * The deep link is what other screens rely on to send someone to a due date.
 * If it lands on the default tab instead, an item outside the next 30 days is
 * simply not on screen and the link looks broken.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ComplianceItem, ComplianceTemplate } from "@/types/compliance"
import { CompliancePage, complianceItemHref } from "../CompliancePage"
import { findSettingsItem } from "../../registry"

const searchParams = vi.hoisted(() => ({ current: new URLSearchParams() }))
const useComplianceItems = vi.hoisted(() => vi.fn())

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams.current,
}))

vi.mock("@/hooks/useCompliance", () => ({
  useComplianceItems: (...args: unknown[]) => useComplianceItems(...args),
  useComplianceTemplates: () => ({ data: [LICENSE_TEMPLATE] }),
  useCompleteComplianceItem: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateComplianceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useUpdateComplianceItem: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

// The supervision card fetches its own data and is not what is under test.
vi.mock("@/components/compliance/SupervisionHeroCard", () => ({ SupervisionHeroCard: () => null }))

const LICENSE_TEMPLATE: ComplianceTemplate = {
  item_type: "license",
  label: "Professional license",
  description: "",
  cadence_days: null,
  reminder_windows: [90, 60, 30, 0],
  multi_instance: true,
  min_edition: "core",
  sort_order: 10,
}

function makeItem(overrides: Partial<ComplianceItem>): ComplianceItem {
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

const SOON = makeItem({ id: "soon", label: "Ohio license", due_date: "2026-05-20" })
// Two months out: in the "Next 90" horizon, so not on the default tab.
const LATER = makeItem({
  id: "later",
  label: "CAQH attestation",
  due_date: "2026-07-10",
  notes: "Attest on the CAQH portal.",
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <CompliancePage />
    </QueryClientProvider>,
  )
}

describe("CompliancePage", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-05-07T12:00:00Z"))
    searchParams.current = new URLSearchParams()
    useComplianceItems.mockReturnValue({ data: [SOON, LATER], isLoading: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("is registered under Practice as Compliance", () => {
    const item = findSettingsItem("compliance")
    expect(item?.groupId).toBe("practice")
    expect(item?.label).toBe("Compliance")
    expect(item?.page).toBe(CompliancePage)
  })

  it("shows the panel without repeating the page title", () => {
    renderPage()

    expect(screen.getByRole("button", { name: "Add reminder" })).toBeInTheDocument()
    expect(screen.getByText("Ohio license")).toBeInTheDocument()
    // The settings header already says "Compliance"; the panel's own h2 would say it twice.
    expect(screen.queryByRole("heading", { name: "Compliance" })).not.toBeInTheDocument()
    // Nothing is expanded without a link asking for it.
    expect(screen.queryByTestId("compliance-item-details")).not.toBeInTheDocument()
  })

  it("opens on the linked item, on its own horizon, expanded", () => {
    searchParams.current = new URLSearchParams("item=later")
    renderPage()

    const row = screen.getByTestId("compliance-item-later")
    expect(row).toHaveAttribute("aria-current", "true")
    const details = within(row).getByTestId("compliance-item-details")
    expect(details).toHaveTextContent("Due July 10, 2026")
    expect(details).toHaveTextContent("Attest on the CAQH portal.")
    // The tab moved to the item's horizon, so the urgent item is not listed.
    expect(screen.queryByText("Ohio license")).not.toBeInTheDocument()
  })

  it("ignores a link to an item that no longer exists", () => {
    searchParams.current = new URLSearchParams("item=gone")
    renderPage()

    expect(screen.getByText("Ohio license")).toBeInTheDocument()
    expect(screen.queryByTestId("compliance-item-details")).not.toBeInTheDocument()
  })

  it("builds the link the page reads", () => {
    expect(complianceItemHref("a b")).toBe("/dashboard/settings/compliance?item=a%20b")
  })
})
