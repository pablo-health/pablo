// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { SupervisionHeroCard } from "../SupervisionHeroCard"
import type {
  SupervisionRelationship,
  SupervisionHoursEntry,
} from "@/types/supervision"

// ---------------------------------------------------------------------------
// Hook mocks
// ---------------------------------------------------------------------------

const useSupervisionRelationships = vi.hoisted(() => vi.fn())
const useSupervisionHours = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useSupervision", () => ({
  useSupervisionRelationships: (...args: unknown[]) =>
    useSupervisionRelationships(...args),
  useSupervisionHours: (...args: unknown[]) => useSupervisionHours(...args),
  useCreateSupervisionRelationship: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useUpdateSupervisionRelationship: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useDeleteSupervisionRelationship: () => ({
    mutateAsync: vi.fn(),
    isPending: false,
  }),
  useAddSupervisionHours: () => ({ mutateAsync: vi.fn(), isPending: false }),
}))

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeRelationship(
  overrides: Partial<SupervisionRelationship> = {},
): SupervisionRelationship {
  return {
    id: "rel-1",
    relationship_type: "clinical_supervision",
    supervisor_name: "Dr. Jane Doe",
    supervisor_credential: "LCSW",
    supervisor_dea: null,
    supervisor_license: null,
    state: "NY",
    effective_date: "2026-01-01",
    review_cadence_days: 90,
    next_review_date: null,
    authority_ref: null,
    status: "active",
    ...overrides,
  }
}

function makeHoursEntry(
  overrides: Partial<SupervisionHoursEntry> = {},
): SupervisionHoursEntry {
  return {
    id: "hrs-1",
    logged_date: "2026-05-01",
    hours: 2.5,
    kind: "individual",
    supervisor: "Dr. Jane Doe",
    notes: null,
    ...overrides,
  }
}

function renderCard() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <SupervisionHeroCard />
    </QueryClientProvider>,
  )
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("SupervisionHeroCard", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-05-07T12:00:00Z"))
    useSupervisionHours.mockReturnValue({ data: [], isLoading: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("renders nothing when there are no supervision relationships", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [],
      isLoading: false,
    })

    const { container } = renderCard()
    expect(container.firstChild).toBeNull()
  })

  it("renders nothing while loading", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [],
      isLoading: true,
    })

    const { container } = renderCard()
    expect(container.firstChild).toBeNull()
  })

  it("shows supervisor name and credential", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship()],
      isLoading: false,
    })

    renderCard()

    expect(screen.getByText("Dr. Jane Doe")).toBeInTheDocument()
    expect(screen.getByText("LCSW")).toBeInTheDocument()
  })

  it("shows the active status badge", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship({ status: "active" })],
      isLoading: false,
    })

    renderCard()

    expect(screen.getByText("active")).toBeInTheDocument()
  })

  it("shows lapsed status badge with correct styling", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship({ status: "lapsed" })],
      isLoading: false,
    })

    renderCard()

    const badge = screen.getByText("lapsed")
    expect(badge).toBeInTheDocument()
    // Lapsed uses rose styling
    expect(badge.className).toMatch(/rose/)
  })

  it("shows the next review date countdown when next_review_date is set", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship({ next_review_date: "2026-05-14" })], // 7 days
      isLoading: false,
    })

    renderCard()

    expect(screen.getByText("due in 7 days")).toBeInTheDocument()
  })

  it("shows overdue badge for past review dates", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship({ next_review_date: "2026-04-30" })], // -7 days
      isLoading: false,
    })

    renderCard()

    expect(screen.getByText("7 days overdue")).toBeInTheDocument()
  })

  it("shows supervisor DEA and license when present", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [
        makeRelationship({
          supervisor_dea: "AB1234567",
          supervisor_license: "NY-12345",
        }),
      ],
      isLoading: false,
    })

    renderCard()

    expect(screen.getByText("AB1234567")).toBeInTheDocument()
    expect(screen.getByText("NY-12345")).toBeInTheDocument()
  })

  it("renders multiple relationships as separate cards", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [
        makeRelationship({ id: "rel-1", supervisor_name: "Dr. Alice" }),
        makeRelationship({
          id: "rel-2",
          supervisor_name: "Dr. Bob",
          relationship_type: "prescriptive_authority",
        }),
      ],
      isLoading: false,
    })

    renderCard()

    expect(screen.getByText("Dr. Alice")).toBeInTheDocument()
    expect(screen.getByText("Dr. Bob")).toBeInTheDocument()
    expect(screen.getAllByTestId("supervision-hero-card")).toHaveLength(2)
  })

  it("shows the hours accordion for clinical_supervision relationships", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship({ relationship_type: "clinical_supervision" })],
      isLoading: false,
    })

    renderCard()

    expect(
      screen.getByRole("button", { name: /accrued hours/i }),
    ).toBeInTheDocument()
  })

  it("does not show the hours accordion for non-clinical relationships", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [
        makeRelationship({ relationship_type: "prescriptive_authority" }),
      ],
      isLoading: false,
    })

    renderCard()

    expect(
      screen.queryByRole("button", { name: /accrued hours/i }),
    ).not.toBeInTheDocument()
  })

  it("expands the hours panel and shows logged entries", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship({ id: "rel-1" })],
      isLoading: false,
    })
    useSupervisionHours.mockReturnValue({
      data: [
        makeHoursEntry({ hours: 2.5, kind: "individual" }),
        makeHoursEntry({
          id: "hrs-2",
          hours: 1.0,
          kind: "group",
          logged_date: "2026-05-10",
        }),
      ],
      isLoading: false,
    })

    renderCard()

    fireEvent.click(screen.getByRole("button", { name: /accrued hours/i }))

    // Data is mocked synchronously — no async wait needed.
    expect(screen.getByText("3.5 hrs total")).toBeInTheDocument()
    expect(screen.getByText("2.5 hrs")).toBeInTheDocument()
    expect(screen.getByText("1.0 hrs")).toBeInTheDocument()
  })

  it("shows 'no hours logged' when entry list is empty after expand", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [makeRelationship()],
      isLoading: false,
    })
    useSupervisionHours.mockReturnValue({ data: [], isLoading: false })

    renderCard()

    fireEvent.click(screen.getByRole("button", { name: /accrued hours/i }))

    // Data is mocked synchronously — no async wait needed.
    expect(screen.getByText(/no hours logged yet/i)).toBeInTheDocument()
  })

  it("shows authority reference when present", () => {
    useSupervisionRelationships.mockReturnValue({
      data: [
        makeRelationship({ authority_ref: "21 CFR 1301.28" }),
      ],
      isLoading: false,
    })

    renderCard()

    expect(screen.getByText(/21 CFR 1301.28/)).toBeInTheDocument()
  })
})
