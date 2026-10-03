// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The first-run hours step saving a week into the real page, through the
 * real query hooks and cache — only the network is replaced. The page and
 * the step each have their own tests with the other mocked out; this is the
 * seam between them, where the rule list refetching mid-save used to take
 * the step away before it had finished.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { AvailabilityRule, CreateAvailabilityRuleRequest } from "@/types/availability"
import CalendarPage from "../page"

vi.mock("@/lib/config", () => ({ useConfig: () => ({ googleCalendarEnabled: false }) }))
vi.mock("@/lib/auth-context", () => ({ useAuth: () => ({ loading: false }) }))
vi.mock("@/components/theme/ThemeProvider", () => ({ useTheme: () => ({ theme: "warm-paper" }) }))
vi.mock("@/lib/access/readOnlyMode", () => ({ useReadOnlyMode: () => ({ readOnly: false }) }))
vi.mock("@/lib/api/scheduling", () => ({
  getICalSyncStatus: vi.fn().mockResolvedValue({ connections: [] }),
  triggerICalSync: vi.fn(),
}))
vi.mock("@/components/calendar/editorial", () => ({
  EditorialCalendar: () => <div data-testid="editorial-calendar" />,
}))
vi.mock("@/components/calendar/AppointmentModal", () => ({ AppointmentModal: () => null }))

const server = vi.hoisted(() => ({
  rules: [] as AvailabilityRule[],
  // Sizes of the rule list as each fetch of it returned.
  listed: [] as number[],
  // One pending create per weekday, settled by the test.
  creates: new Map<number, { resolve: () => void; reject: () => void }>(),
}))

vi.mock("@/lib/api/availability", () => ({
  listAvailabilityRules: async () => {
    const data = [...server.rules]
    server.listed.push(data.length)
    return { data, total: data.length }
  },
  createAvailabilityRule: (request: CreateAvailabilityRuleRequest) =>
    new Promise<AvailabilityRule>((resolve, reject) => {
      const day = Number(request.params.day_of_week)
      const rule = {
        ...request,
        id: `rule-${day}`,
        user_id: "u1",
        created_at: null,
        updated_at: null,
      } as AvailabilityRule
      server.creates.set(day, {
        resolve: () => {
          server.rules.push(rule)
          resolve(rule)
        },
        reject: () => reject(new Error("503")),
      })
    }),
  getFreeSlots: vi.fn(),
  parseAvailabilityRules: vi.fn(),
  updateAvailabilityRule: vi.fn(),
  deleteAvailabilityRule: vi.fn(),
  checkConflicts: vi.fn(),
}))

vi.mock("@/lib/api/users", () => ({
  getPreferences: async () => ({
    timezone: "America/New_York",
    calendar_default_view: "timeGridWeek",
    calendar_setup_complete: true,
  }),
  savePreferences: async (prefs: unknown) => prefs,
}))

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <CalendarPage />
    </QueryClientProvider>
  )
}

describe("CalendarPage saving first-run hours", () => {
  beforeEach(() => {
    server.rules = []
    server.listed = []
    server.creates.clear()
  })

  it("keeps the step until every create settles, and reports the one that failed", async () => {
    const user = userEvent.setup()
    renderPage()

    await user.click(await screen.findByRole("button", { name: "Pick from a grid instead" }))
    // The grid starts on Monday to Friday, 9 to 5: five rules.
    await user.click(screen.getByRole("button", { name: "Save these hours" }))
    await waitFor(() => expect(server.creates.size).toBe(5))

    // Monday lands first, and the rule list refetches with it in.
    await act(async () => server.creates.get(0)!.resolve())
    await waitFor(() => expect(server.listed.at(-1)).toBe(1))

    expect(screen.getByText("When do you see clients?")).toBeInTheDocument()
    expect(screen.queryByTestId("editorial-calendar")).not.toBeInTheDocument()

    // Wednesday — the third rule — fails; the rest land.
    await act(async () => {
      server.creates.get(1)!.resolve()
      server.creates.get(2)!.reject()
      server.creates.get(3)!.resolve()
      server.creates.get(4)!.resolve()
    })

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Some of those hours could not be saved. Try again to save the rest."
    )
    expect(screen.queryByTestId("editorial-calendar")).not.toBeInTheDocument()

    // The retry sends Wednesday alone, and only then does the calendar open.
    server.creates.clear()
    await user.click(screen.getByRole("button", { name: "Save these hours" }))
    await waitFor(() => expect([...server.creates.keys()]).toEqual([2]))
    await act(async () => server.creates.get(2)!.resolve())

    expect(await screen.findByTestId("editorial-calendar")).toBeInTheDocument()
    expect(server.rules.map((rule) => rule.params.day_of_week).sort()).toEqual([0, 1, 2, 3, 4])
  })
})
