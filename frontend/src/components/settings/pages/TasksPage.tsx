// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Practice > Tasks. Licenses, attestations and renewals, and anything else
 * the clinician wants reminding of.
 *
 * The same panel the dashboard shows beside the week, given a page of its own
 * so it has an address: a due date elsewhere in the app links here with
 * `?item=<id>` and the page opens on that task; `?category=compliance` opens
 * it on one category. Both surfaces read and write the same tasks.
 */

import { useSearchParams } from "next/navigation"
import { TasksPanel } from "@/components/tasks/TasksPanel"
import { isTaskCategory } from "@/types/tasks"

/** The query parameter a link uses to open the page on one task. */
export const TASK_ITEM_PARAM = "item"

/** The query parameter a link uses to open the page on one category. */
export const TASK_CATEGORY_PARAM = "category"

/** The settings section this page lives at. */
export const TASKS_SECTION = "tasks"

/** Where a link to one task points. */
export function taskHref(taskId: string): string {
  return `/dashboard/settings/${TASKS_SECTION}?${TASK_ITEM_PARAM}=${encodeURIComponent(taskId)}`
}

/** The old names, for code that links to a compliance item: it is a task now. */
export const COMPLIANCE_ITEM_PARAM = TASK_ITEM_PARAM
export const complianceItemHref = taskHref

export function TasksPage() {
  const params = useSearchParams()
  const focusItemId = params?.get(TASK_ITEM_PARAM) ?? null
  const category = params?.get(TASK_CATEGORY_PARAM)
  return (
    <TasksPanel
      focusItemId={focusItemId}
      initialCategory={isTaskCategory(category) ? category : "all"}
      variant="page"
    />
  )
}
