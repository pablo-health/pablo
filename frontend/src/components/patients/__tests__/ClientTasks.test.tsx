// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client's open tasks on their chart: read for that client only, the done
 * ones left out, and "Add a task" starting tied to them.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen } from "@testing-library/react"
import { ClientTasks } from "../ClientTasks"
import type { Task } from "@/types/tasks"

const useTasks = vi.hoisted(() => vi.fn())

vi.mock("@/hooks/useTasks", () => ({
  useTasks: (...args: unknown[]) => useTasks(...args),
  useCompleteTask: () => ({ mutate: vi.fn(), isPending: false }),
  useCreateTask: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
  useUpdateTask: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false }),
}))
vi.mock("@/hooks/usePatients", () => ({
  usePatientList: () => ({ data: undefined }),
}))

const RELEASE: Task = {
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
}

beforeEach(() => {
  vi.clearAllMocks()
  useTasks.mockReturnValue({ data: [RELEASE] })
})

describe("ClientTasks", () => {
  it("lists only this client's open tasks", () => {
    render(<ClientTasks patientId="p-omar" patientName="Omar Haddad" />)

    expect(useTasks).toHaveBeenCalledWith({ patient_id: "p-omar", open_only: true })
    expect(screen.getByText("Send the release form")).toBeInTheDocument()
    // The client is the page; the row does not name them again.
    expect(screen.queryByTestId("task-client-name")).not.toBeInTheDocument()
  })

  it("adds a task already tied to the client", () => {
    render(<ClientTasks patientId="p-omar" patientName="Omar Haddad" />)

    fireEvent.click(screen.getByRole("button", { name: "Add a task" }))
    expect(screen.getByTestId("task-client")).toHaveTextContent("Omar Haddad")
  })
})
