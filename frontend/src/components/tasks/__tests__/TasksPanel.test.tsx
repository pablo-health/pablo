// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { TasksPanel } from "../TasksPanel"
import type { ComplianceTemplate } from "@/types/compliance"
import type { Task } from "@/types/tasks"

const useTasks = vi.hoisted(() => vi.fn())
const useTaskTemplates = vi.hoisted(() => vi.fn())
const useCompleteTask = vi.hoisted(() => vi.fn())
const createTask = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))

vi.mock("@/hooks/useTasks", () => ({
  useTasks: (...args: unknown[]) => useTasks(...args),
  useTaskTemplates: (...args: unknown[]) => useTaskTemplates(...args),
  useCompleteTask: (...args: unknown[]) => useCompleteTask(...args),
  useCreateTask: () => ({ mutateAsync: createTask, isPending: false, isError: false }),
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
  useTaskDocuments: () => ({ data: [], isLoading: false }),
  useUploadTaskDocument: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeleteTaskDocument: () => ({ mutateAsync: vi.fn(), isPending: false }),
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

function makeTask(overrides: Partial<Task> = {}): Task {
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

function given(tasks: Task[] | undefined, extra: Record<string, unknown> = {}) {
  useTasks.mockReturnValue({ data: tasks, isLoading: false, ...extra })
  useTaskTemplates.mockReturnValue({ data: [LICENSE_TEMPLATE] })
}

function renderPanel(props: Parameters<typeof TasksPanel>[0] = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <TasksPanel {...props} />
    </QueryClientProvider>,
  )
}

const SOON_LICENCE = makeTask({ id: "soon", due_date: "2026-06-01", label: "NY LMHC renewal" })
const RENT = makeTask({
  id: "rent",
  category: "practice",
  item_type: "task",
  label: "Pay office rent",
  due_date: "2026-05-10",
  repeat_rule: "monthly",
})

describe("TasksPanel", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date("2026-05-07T12:00:00Z"))
    useCompleteTask.mockReturnValue({ mutate: vi.fn(), isPending: false })
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it("is titled Tasks on the dashboard", () => {
    given([SOON_LICENCE])
    renderPanel()
    expect(screen.getByRole("heading", { name: "Tasks" })).toBeInTheDocument()
  })

  it("starts a first task in Compliance and offers the reminder list", () => {
    given([])
    renderPanel()

    expect(screen.getByText(/let's set up your reminders/i)).toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Add a task" }))

    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByRole("radio", { name: "Compliance" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    fireEvent.click(within(dialog).getByRole("button", { name: "Choose it from the list" }))
    // The reminder catalog, which knows each template's fields.
    expect(screen.getByText("Professional license")).toBeInTheDocument()
  })

  // A failed load used to show the first-time setup screen to someone who may
  // already have tasks saved.
  it("says the tasks didn't load instead of the first-time setup screen", () => {
    const refetch = vi.fn()
    given(undefined, { isError: true, refetch })

    renderPanel()

    expect(screen.getByText("Your tasks didn’t load.")).toBeInTheDocument()
    expect(screen.queryByText(/let's set up your reminders/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "Try again" }))
    expect(refetch).toHaveBeenCalledTimes(1)
  })

  it("shows the all-clear state when tasks are saved but none due soon", () => {
    given([makeTask({ due_date: "2027-12-01" })])

    renderPanel()

    expect(
      screen.getByText(/you'?re all caught up\. pablo'?s got it from here\./i),
    ).toBeInTheDocument()
  })

  it("shows a non-compliance task due soon beside the compliance ones, by date", () => {
    given([
      SOON_LICENCE, // ~25 days
      makeTask({ id: "overdue", due_date: "2026-04-15" }),
      RENT, // 3 days
      makeTask({ id: "later", due_date: "2027-01-01" }), // beyond the urgent tab
    ])

    renderPanel()

    const rows = screen.getAllByRole("listitem")
    expect(rows.map((r) => r.getAttribute("data-testid"))).toEqual([
      "compliance-item-overdue",
      "compliance-item-rent",
      "compliance-item-soon",
    ])
    expect(rows[1]).toHaveTextContent("Pay office rent")
    expect(rows[1]).toHaveTextContent("due in 3 days")
  })

  it("filters by category, Compliance first", () => {
    given([SOON_LICENCE, RENT])

    renderPanel()

    const filter = screen.getByRole("radiogroup", { name: "Show tasks" })
    const options = within(filter).getAllByRole("radio")
    expect(options.map((o) => o.textContent)).toEqual([
      "Compliance · 1",
      "Practice · 1",
      "Other · 0",
      "All",
    ])

    fireEvent.click(within(filter).getByRole("radio", { name: /Practice/ }))
    expect(screen.getByTestId("compliance-item-rent")).toBeInTheDocument()
    expect(screen.queryByTestId("compliance-item-soon")).not.toBeInTheDocument()

    fireEvent.click(within(filter).getByRole("radio", { name: /Compliance/ }))
    expect(screen.getByTestId("compliance-item-soon")).toBeInTheDocument()
    expect(screen.queryByTestId("compliance-item-rent")).not.toBeInTheDocument()
  })

  it("opens on the category a link asked for", () => {
    given([SOON_LICENCE, RENT])
    renderPanel({ initialCategory: "compliance" })
    expect(screen.queryByTestId("compliance-item-rent")).not.toBeInTheDocument()
    expect(screen.getByTestId("compliance-item-soon")).toBeInTheDocument()
  })

  it("offers evidence documents on compliance tasks only", () => {
    given([SOON_LICENCE, RENT])
    renderPanel()
    expect(screen.getByRole("button", { name: "Documents for NY LMHC renewal" })).toBeInTheDocument()
    expect(
      screen.queryByRole("button", { name: "Documents for Pay office rent" }),
    ).not.toBeInTheDocument()
  })

  it("drops a finished to-do and keeps the next occurrence it filed", () => {
    given([
      { ...RENT, completed_at: "2026-05-06T10:00:00Z" },
      { ...RENT, id: "rent-next", due_date: "2026-06-10" },
    ])
    renderPanel()

    fireEvent.click(screen.getByRole("radio", { name: /^All/ }))
    expect(screen.queryByTestId("compliance-item-rent")).not.toBeInTheDocument()
    expect(screen.getByRole("radio", { name: /Practice/ })).toHaveTextContent("Practice · 1")
  })

  it("marks a task done", () => {
    const mutate = vi.fn()
    useCompleteTask.mockReturnValue({ mutate, isPending: false })
    given([RENT])

    renderPanel()
    fireEvent.click(screen.getByRole("button", { name: /mark done/i }))

    expect(mutate).toHaveBeenCalledWith("rent")
  })

  it("edits a compliance task in the reminder form and any other in the task form", () => {
    given([SOON_LICENCE, RENT])

    renderPanel()

    fireEvent.click(screen.getByRole("button", { name: /edit ny lmhc renewal/i }))
    expect(screen.getByText(/edit · ny lmhc renewal/i)).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })

    fireEvent.click(screen.getByRole("button", { name: /edit pay office rent/i }))
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByText("Edit task")).toBeInTheDocument()
    expect(within(dialog).getByLabelText("Title")).toHaveValue("Pay office rent")
    expect(within(dialog).getByLabelText("Repeats")).toHaveValue("monthly")
  })

  it("adds a compliance reminder from the Compliance filter, and a task from the others", () => {
    given([SOON_LICENCE, RENT])
    renderPanel()

    fireEvent.click(screen.getByRole("radio", { name: /Compliance/ }))
    fireEvent.click(screen.getByRole("button", { name: "Add a task" }))
    expect(screen.getByText(/add or edit a reminder/i)).toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" })

    fireEvent.click(screen.getByRole("radio", { name: /Practice/ }))
    fireEvent.click(screen.getByRole("button", { name: "Add a task" }))
    const dialog = screen.getByRole("dialog")
    expect(within(dialog).getByRole("radio", { name: "Practice" })).toHaveAttribute(
      "aria-checked",
      "true",
    )
  })
})
