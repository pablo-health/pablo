// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { useMemo, useState } from "react"
import { ComplianceItemRow } from "@/components/compliance/ComplianceItemRow"
import { DocumentsDialog } from "@/components/compliance/DocumentsDialog"
import { HorizonStrip } from "@/components/compliance/HorizonStrip"
import { ReminderComposer } from "@/components/compliance/ReminderComposer"
import { SupervisionHeroCard } from "@/components/compliance/SupervisionHeroCard"
import {
  type EnrichedItem,
  type HorizonId,
  enrichItems,
  sortByDueDate,
} from "@/components/compliance/horizons"
import { PanelLoadError } from "@/components/support/PanelLoadError"
import { Button } from "@/components/ui/button"
import { useCompleteTask, useTasks, useTaskTemplates } from "@/hooks/useTasks"
import type { ComplianceTemplate } from "@/types/compliance"
import type { Task, TaskCategory } from "@/types/tasks"
import { type CategorySelection, CategoryFilter } from "./CategoryFilter"
import { TaskComposer } from "./TaskComposer"

type Selection = HorizonId | "urgent" | "all"

export interface TasksPanelProps {
  /**
   * A task to open on arrival: its horizon is selected, its row expanded and
   * scrolled to. Other screens link here with `?item=<id>`; an id that matches
   * nothing is ignored.
   */
  focusItemId?: string | null
  /** The category shown first. Everything, unless a link asked for one. */
  initialCategory?: CategorySelection
  /**
   * "page" when the panel is the whole of a settings page, whose header
   * already names it; the panel then drops its own title.
   */
  variant?: "panel" | "page"
}

/** The horizon tab that shows a given horizon's tasks. */
function selectionFor(horizon: HorizonId): Selection {
  if (horizon === "overdue" || horizon === "week" || horizon === "month") return "urgent"
  if (horizon === "informational") return "all"
  return horizon
}

const EMPTY_COUNTS: Record<HorizonId, number> = {
  overdue: 0,
  week: 0,
  month: 0,
  quarter: 0,
  beyond: 0,
  informational: 0,
}

type Composer =
  | { kind: "closed" }
  | { kind: "task"; task: Task | null; defaultCategory?: TaskCategory }
  | { kind: "reminder"; task: Task | null }

/** Tasks by horizon: compliance reminders and the clinician's own, one list. */
export function TasksPanel({
  focusItemId = null,
  initialCategory = "all",
  variant = "panel",
}: TasksPanelProps = {}) {
  const { data: tasksData, isLoading, isError, refetch } = useTasks()
  // A done to-do is done, and a repeating one has already filed its next
  // occurrence. A compliance task keeps the place it always had: its
  // renewal is tracked on the row itself.
  const tasks = useMemo(
    () => (tasksData ?? []).filter((t) => t.category === "compliance" || !t.completed_at),
    [tasksData],
  )
  const { data: templates = [] } = useTaskTemplates()
  const completeTask = useCompleteTask()
  const [composer, setComposer] = useState<Composer>({ kind: "closed" })
  const [docsTask, setDocsTask] = useState<Task | null>(null)
  const [category, setCategory] = useState<CategorySelection>(initialCategory)
  // Null until the person picks a tab, so the focused task's horizon can be
  // the starting tab once the tasks arrive, without an effect to sync it.
  const [chosen, setChosen] = useState<Selection | null>(null)

  const templateByType = useMemo(() => {
    const m = new Map<string, ComplianceTemplate>()
    for (const t of templates) m.set(t.item_type, t)
    return m
  }, [templates])

  const compliance = useMemo(() => tasks.filter((t) => t.category === "compliance"), [tasks])
  const categoryCounts = useMemo(() => {
    const c: Record<TaskCategory, number> = { compliance: 0, practice: 0, other: 0 }
    for (const t of tasks) if (t.category in c) c[t.category]++
    return c
  }, [tasks])

  const enriched = useMemo(
    () =>
      enrichItems(
        tasks.filter((t) => category === "all" || t.category === category),
        templateByType,
      ),
    [tasks, category, templateByType],
  )

  const focused = useMemo(() => {
    if (!focusItemId) return undefined
    const task = tasks.find((t) => t.id === focusItemId)
    return task ? enrichItems([task], templateByType)[0] : undefined
  }, [tasks, focusItemId, templateByType])
  const selection: Selection = chosen ?? (focused ? selectionFor(focused.horizon) : "urgent")

  const counts = useMemo(() => {
    const c = { ...EMPTY_COUNTS }
    for (const e of enriched) c[e.horizon]++
    return c
  }, [enriched])

  // Must match HorizonStrip's urgentCount (overdue + week + month), not isUrgent.
  const urgent = useMemo(
    () =>
      enriched
        .filter((e) => e.horizon === "overdue" || e.horizon === "week" || e.horizon === "month")
        .sort(sortByDueDate),
    [enriched],
  )

  const visible = useMemo(() => {
    let list: EnrichedItem<Task>[]
    if (selection === "urgent") list = urgent
    else if (selection === "all") {
      list = enriched.filter((e) => e.horizon !== "informational").sort(sortByDueDate)
    } else {
      list = enriched.filter((e) => e.horizon === selection).sort(sortByDueDate)
    }
    // A task with no date, or in another category, sits in no tab; a link
    // to it still shows it.
    if (focused && !list.some((e) => e.item.id === focused.item.id)) list = [...list, focused]
    return list
  }, [selection, enriched, urgent, focused])

  function openAdd() {
    setComposer(
      category === "compliance" ? { kind: "reminder", task: null } : { kind: "task", task: null },
    )
  }

  /** The first task: compliance is the likeliest, so the form starts there and offers the list. */
  function openFirst() {
    setComposer({ kind: "task", task: null, defaultCategory: "compliance" })
  }

  function openEdit(task: Task) {
    // A compliance task with a template edits in the reminder form, which
    // knows the template's fields; anything else in the task form.
    const followsTemplate = task.category === "compliance" && templateByType.has(task.item_type)
    setComposer({ kind: followsTemplate ? "reminder" : "task", task })
  }

  const closeComposer = (open: boolean) => {
    if (!open) setComposer({ kind: "closed" })
  }

  const dialogs = (
    <>
      <TaskComposer
        open={composer.kind === "task"}
        onOpenChange={closeComposer}
        task={composer.kind === "task" ? composer.task : null}
        defaultCategory={
          (composer.kind === "task" && composer.defaultCategory) ||
          (category === "all" ? "practice" : category)
        }
        onChooseComplianceReminder={() => setComposer({ kind: "reminder", task: null })}
      />
      <ReminderComposer
        open={composer.kind === "reminder"}
        onOpenChange={closeComposer}
        templates={templates}
        items={compliance}
        initialItem={composer.kind === "reminder" ? composer.task : null}
      />
      {docsTask && (
        <DocumentsDialog
          open={docsTask !== null}
          onOpenChange={(open) => !open && setDocsTask(null)}
          item={docsTask}
        />
      )}
    </>
  )

  if (isLoading) {
    return (
      <div className="card">
        <PanelHeader onAdd={openAdd} hasAny={false} variant={variant} />
        <p className="text-sm text-neutral-500 py-6 text-center">Loading…</p>
      </div>
    )
  }

  // Without this a failed load showed the first-time screen to someone who
  // may already have tasks.
  if (isError && !tasksData) {
    return (
      <div className="card">
        <PanelHeader onAdd={openAdd} hasAny={false} variant={variant} />
        <PanelLoadError message="Your tasks didn’t load." onRetry={() => void refetch()} />
      </div>
    )
  }

  const showsCompliance = category === "all" || category === "compliance"

  if (tasks.length === 0) {
    return (
      <div className="card">
        <PanelHeader onAdd={openAdd} hasAny={false} variant={variant} />
        {showsCompliance && <SupervisionHeroCard />}
        <EmptyState onStart={category === "all" ? openFirst : openAdd} />
        {dialogs}
      </div>
    )
  }

  const showAllClear = selection === "urgent" && urgent.length === 0 && !focused

  return (
    <div className="card">
      <PanelHeader onAdd={openAdd} hasAny variant={variant} />
      <CategoryFilter selected={category} onSelect={setCategory} counts={categoryCounts} />
      {showsCompliance && <SupervisionHeroCard />}
      <HorizonStrip counts={counts} selected={selection} onSelect={setChosen} />

      {showAllClear ? (
        <AllClear />
      ) : visible.length === 0 ? (
        <EmptyBucket />
      ) : (
        <ul className="space-y-1.5" role="list">
          {visible.map((entry) => (
            <ComplianceItemRow
              key={entry.item.id}
              entry={entry}
              expanded={entry.item.id === focused?.item.id}
              onEdit={() => openEdit(entry.item)}
              onOpenDocs={
                entry.item.category === "compliance" ? () => setDocsTask(entry.item) : undefined
              }
              onComplete={() => completeTask.mutate(entry.item.id)}
              completing={completeTask.isPending}
            />
          ))}
        </ul>
      )}

      {dialogs}
    </div>
  )
}

function PanelHeader({
  onAdd,
  hasAny,
  variant,
}: {
  onAdd: () => void
  hasAny: boolean
  variant: "panel" | "page"
}) {
  if (variant === "page") {
    if (!hasAny) return null
    return (
      <div className="flex justify-end mb-4">
        <Button variant="outline" size="sm" onClick={onAdd}>
          Add a task
        </Button>
      </div>
    )
  }
  return (
    <div className="flex items-start justify-between mb-4">
      <div>
        <h2 className="text-xl font-display font-semibold text-neutral-900">Tasks</h2>
        <p className="text-sm text-neutral-600 mt-1">
          Renewals, deadlines and anything else with a date.
        </p>
      </div>
      {hasAny && (
        <Button variant="outline" size="sm" onClick={onAdd}>
          Add a task
        </Button>
      )}
    </div>
  )
}

const REMINDER_MONTHS = [
  "jan", "feb", "mar", "apr", "may", "jun",
  "jul", "aug", "sep", "oct", "nov", "dec",
] as const

function EmptyState({ onStart }: { onStart: () => void }) {
  // Pablo holds up a desk calendar showing the current month.
  const month = useMemo(() => REMINDER_MONTHS[new Date().getMonth()], [])
  return (
    <div className="flex flex-col items-center text-center py-6">
      <Image
        src={`/pablo-reminders-${month}.webp`}
        alt="Pablo bear, your documentation companion"
        width={96}
        height={96}
        priority
      />
      <p className="font-display text-lg text-neutral-900 mt-3">
        Let&apos;s set up your reminders
      </p>
      <p className="text-sm text-neutral-600 mt-1 max-w-sm">
        Add your license, insurance and attestations, or anything else with a
        date. Pablo shows each one here as it comes due.
      </p>
      <Button className="mt-4" onClick={onStart}>
        Add a task
      </Button>
    </div>
  )
}

function AllClear() {
  return (
    <div className="flex flex-col items-center text-center py-6">
      <Image src="/pablo-tie.webp" alt="Pablo bear" width={64} height={64} />
      <p className="text-sm text-neutral-700 mt-3">
        You&apos;re all caught up. Pablo&apos;s got it from here.
      </p>
    </div>
  )
}

function EmptyBucket() {
  return (
    <p className="text-sm text-neutral-500 py-6 text-center">
      Nothing in this horizon. Try another bucket above.
    </p>
  )
}
