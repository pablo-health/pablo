// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice > Tasks — the panel on a page of its own, the link that opens it on
 * one task, and the old Compliance address that now lands here.
 *
 * The deep link is what other screens rely on to send someone to a due date.
 * If it lands on the default tab instead, a task outside the next 30 days is
 * simply not on screen and the link looks broken.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { ComplianceTemplate } from "@/types/compliance"
import type { Task } from "@/types/tasks"
import { TasksPage, complianceItemHref, taskHref } from "../TasksPage"
import { findSettingsItem, movedSettingsHref } from "../../registry"

const searchParams = vi.hoisted(() => ({ current: new URLSearchParams() }))
const useTasks = vi.hoisted(() => vi.fn())

vi.mock("next/navigation", () => ({
  useSearchParams: () => searchParams.current,
}))

vi.mock("@/hooks/useTasks", () => ({
  useTasks: (...args: unknown[]) => useTasks(...args),
  useTaskTemplates: () => ({ data: [LICENSE_TEMPLATE] }),
  useCompleteTask: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTask: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
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

function makeTask(overrides: Partial<Task>): Task {
  return {
    id: "item-1",
    category: "compliance",
    item_type: "license",
    label: "Professional license",
    due_date: null,
    notes: null,
    repeat_rule: null,
    parent_task_id: null,
    goal_key: null,
    source: "manual",
    completed_at: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  }
}

const SOON = makeTask({ id: "soon", label: "Ohio license", due_date: "2026-05-20" })
// Two months out: in the "Next 90" horizon, so not on the default tab.
const LATER = makeTask({
  id: "later",
  label: "CAQH attestation",
  due_date: "2026-07-10",
  notes: "Attest on the CAQH portal.",
})
const LEASE = makeTask({
  id: "lease",
  category: "practice",
  item_type: "task",
  label: "Renew the office lease",
  due_date: "2026-05-15",
})

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TasksPage />
    </QueryClientProvider>,
  )
}

describe("TasksPage", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-05-07T12:00:00Z"))
    searchParams.current = new URLSearchParams()
    useTasks.mockReturnValue({ data: [SOON, LATER, LEASE], isLoading: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("is registered under Practice as Tasks, and Compliance is no longer its own page", () => {
    const item = findSettingsItem("tasks")
    expect(item?.groupId).toBe("practice")
    expect(item?.label).toBe("Tasks")
    expect(item?.page).toBe(TasksPage)
    expect(findSettingsItem("compliance")).toBeUndefined()
  })

  it("shows every category without repeating the page title", () => {
    renderPage()

    expect(screen.getByRole("button", { name: "Add a task" })).toBeInTheDocument()
    expect(screen.getByText("Ohio license")).toBeInTheDocument()
    expect(screen.getByText("Renew the office lease")).toBeInTheDocument()
    // The settings header already says "Tasks"; the panel's own h2 would say it twice.
    expect(screen.queryByRole("heading", { name: "Tasks" })).not.toBeInTheDocument()
    // Nothing is expanded without a link asking for it.
    expect(screen.queryByTestId("compliance-item-details")).not.toBeInTheDocument()
  })

  it("opens on the category the address names", () => {
    searchParams.current = new URLSearchParams("category=compliance")
    renderPage()

    expect(screen.getByText("Ohio license")).toBeInTheDocument()
    expect(screen.queryByText("Renew the office lease")).not.toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /Compliance/ })).toHaveAttribute(
      "aria-checked",
      "true",
    )
  })

  it("opens on the linked task, on its own horizon, expanded", () => {
    searchParams.current = new URLSearchParams("item=later")
    renderPage()

    const row = screen.getByTestId("compliance-item-later")
    expect(row).toHaveAttribute("aria-current", "true")
    const details = within(row).getByTestId("compliance-item-details")
    expect(details).toHaveTextContent("Due July 10, 2026")
    expect(details).toHaveTextContent("Attest on the CAQH portal.")
    // The tab moved to the task's horizon, so the urgent one is not listed.
    expect(screen.queryByText("Ohio license")).not.toBeInTheDocument()
  })

  it("shows a linked task even when the category shown is another", () => {
    searchParams.current = new URLSearchParams("category=compliance&item=lease")
    renderPage()

    expect(screen.getByTestId("compliance-item-lease")).toHaveAttribute("aria-current", "true")
  })

  it("ignores a link to a task that no longer exists", () => {
    searchParams.current = new URLSearchParams("item=gone")
    renderPage()

    expect(screen.getByText("Ohio license")).toBeInTheDocument()
    expect(screen.queryByTestId("compliance-item-details")).not.toBeInTheDocument()
  })

  it("builds the link the page reads, under both names", () => {
    expect(taskHref("a b")).toBe("/dashboard/settings/tasks?item=a%20b")
    expect(complianceItemHref("a b")).toBe(taskHref("a b"))
  })
})

describe("the old Compliance address", () => {
  it("moves to Tasks, filtered to compliance", () => {
    expect(movedSettingsHref("compliance", new URLSearchParams())).toBe(
      "/dashboard/settings/tasks?category=compliance",
    )
  })

  it("keeps a deep link it carried", () => {
    expect(movedSettingsHref("compliance", new URLSearchParams("item=abc"))).toBe(
      "/dashboard/settings/tasks?item=abc&category=compliance",
    )
  })

  it("leaves a section that has not moved alone", () => {
    expect(movedSettingsHref("tasks", new URLSearchParams())).toBeNull()
    expect(movedSettingsHref("not-a-section", null)).toBeNull()
  })
})
