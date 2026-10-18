// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The client a task is about, in the task form: picked by name, prefilled
 * from a client's chart, removable, and sent as `patient_id`.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { act, fireEvent, render, screen } from "@testing-library/react"
import { TaskComposer } from "../TaskComposer"
import type { Task } from "@/types/tasks"

const createTask = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
const updateTask = vi.hoisted(() => vi.fn().mockResolvedValue(undefined))
const usePatientList = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useTasks", () => ({
  useCreateTask: () => ({ mutateAsync: createTask, isPending: false, isError: false }),
  useUpdateTask: () => ({ mutateAsync: updateTask, isPending: false, isError: false }),
}))
vi.mock("@/hooks/usePatients", () => ({
  usePatientList: (...args: unknown[]) => usePatientList(...args),
}))

const OMAR = { id: "p-omar", first_name: "Omar", last_name: "Haddad" }

function linkedTask(overrides: Partial<Task> = {}): Task {
  return {
    id: "t-1",
    category: "practice",
    item_type: "task",
    label: "Send the release form",
    due_date: null,
    notes: null,
    repeat_rule: null,
    parent_task_id: null,
    goal_key: null,
    source: "manual",
    completed_at: null,
    created_at: "2026-10-01T00:00:00Z",
    updated_at: "2026-10-01T00:00:00Z",
    patient_id: "p-omar",
    patient_name: "Omar Haddad",
    ...overrides,
  }
}

beforeEach(() => {
  vi.clearAllMocks()
  usePatientList.mockReturnValue({ data: { data: [OMAR], total: 1 } })
})

describe("TaskComposer client", () => {
  it("reads no roster until a name is typed", () => {
    render(<TaskComposer open onOpenChange={() => {}} />)

    // A roster read records a view of every client on it; opening the form must not.
    for (const [, , options] of usePatientList.mock.calls) {
      expect(options).toEqual({ enabled: false })
    }
  })

  it("ties a new task to the client picked by name", async () => {
    vi.useFakeTimers()
    render(<TaskComposer open onOpenChange={() => {}} />)

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Send the release form" } })
    const search = screen.getByRole("combobox", { name: /client/i })
    fireEvent.focus(search)
    fireEvent.change(search, { target: { value: "om" } })
    act(() => {
      vi.advanceTimersByTime(300)
    })
    vi.useRealTimers()
    fireEvent.mouseDown(screen.getByRole("option", { name: "Omar Haddad" }))
    expect(screen.getByTestId("task-client")).toHaveTextContent("Omar Haddad")

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save task" }))
    })
    expect(createTask).toHaveBeenCalledWith(
      expect.objectContaining({ label: "Send the release form", patient_id: "p-omar" }),
    )
  })

  it("starts tied to the chart's client, and can be untied", async () => {
    render(
      <TaskComposer
        open
        onOpenChange={() => {}}
        defaultClient={{ id: "p-omar", name: "Omar Haddad" }}
      />,
    )
    expect(screen.getByTestId("task-client")).toHaveTextContent("Omar Haddad")

    fireEvent.change(screen.getByLabelText("Title"), { target: { value: "Order supplies" } })
    fireEvent.click(screen.getByRole("button", { name: "Remove Omar Haddad" }))
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save task" }))
    })
    expect(createTask).toHaveBeenCalledWith(expect.objectContaining({ patient_id: null }))
  })

  it("keeps a linked task's client when it is edited", async () => {
    render(<TaskComposer open onOpenChange={() => {}} task={linkedTask()} />)
    expect(screen.getByTestId("task-client")).toHaveTextContent("Omar Haddad")

    fireEvent.change(screen.getByLabelText("Title"), {
      target: { value: "Send the signed release form" },
    })
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Save task" }))
    })
    expect(updateTask).toHaveBeenCalledWith({
      id: "t-1",
      payload: expect.objectContaining({
        label: "Send the signed release form",
        patient_id: "p-omar",
      }),
    })
  })
})
