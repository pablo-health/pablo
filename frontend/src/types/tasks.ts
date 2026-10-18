// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Task types. Mirrors backend/app/routes/tasks.py.
 *
 * A compliance item is a task with `category: "compliance"`; a `Task` carries
 * every field a `ComplianceItem` does, so code written for compliance items
 * accepts a task unchanged.
 */

export type TaskCategory = "compliance" | "practice" | "other"
export type TaskSource = "manual" | "reminder" | "plan" | "import"
export type RepeatRule = "weekly" | "monthly" | "quarterly" | "yearly"

export interface Task {
  id: string
  label: string
  category: TaskCategory
  /** The compliance template a compliance task follows; `task` on any other. */
  item_type: string
  due_date: string | null
  notes: string | null
  repeat_rule: RepeatRule | null
  parent_task_id: string | null
  goal_key: string | null
  source: TaskSource
  completed_at: string | null
  created_at: string
  updated_at: string
}

export interface TaskPayload {
  label: string
  category: TaskCategory
  item_type?: string | null
  due_date: string | null
  notes: string | null
  repeat_rule?: RepeatRule | null
  parent_task_id?: string | null
  goal_key?: string | null
  source?: TaskSource
}

export interface TaskFilters {
  category?: TaskCategory
  parent_task_id?: string
  goal_key?: string
  source?: TaskSource
}

/** In the order the filter shows them: compliance first. */
export const TASK_CATEGORIES: { id: TaskCategory; label: string }[] = [
  { id: "compliance", label: "Compliance" },
  { id: "practice", label: "Practice" },
  { id: "other", label: "Other" },
]

export function isTaskCategory(value: string | null | undefined): value is TaskCategory {
  return TASK_CATEGORIES.some((c) => c.id === value)
}

export const REPEAT_RULES: { id: RepeatRule; label: string }[] = [
  { id: "weekly", label: "Every week" },
  { id: "monthly", label: "Every month" },
  { id: "quarterly", label: "Every 3 months" },
  { id: "yearly", label: "Every year" },
]

/** A task's editable fields as a payload, for an update that changes some of them. */
export function taskToPayload(task: Task): TaskPayload {
  return {
    label: task.label,
    category: task.category,
    item_type: task.item_type,
    due_date: task.due_date,
    notes: task.notes,
    repeat_rule: task.repeat_rule,
    parent_task_id: task.parent_task_id,
    goal_key: task.goal_key,
  }
}
