// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useCreateTask, useUpdateTask } from "@/hooks/useTasks"
import {
  REPEAT_RULES,
  TASK_CATEGORIES,
  type RepeatRule,
  type Task,
  type TaskCategory,
  taskToPayload,
} from "@/types/tasks"

export interface TaskComposerProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** The task to edit; a new task when absent. */
  task?: Task | null
  /** The category a new task starts in. */
  defaultCategory?: TaskCategory
  /**
   * Opens the list of license, insurance and training reminders instead.
   * Offered while Compliance is the chosen category of a new task.
   */
  onChooseComplianceReminder?: () => void
}

/** Add or edit a task: a title, a category, an optional date and repeat, and notes. */
export function TaskComposer({ open, onOpenChange, ...rest }: TaskComposerProps) {
  // Keyed on `open` so each opening starts from the task it was given.
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <ComposerForm key={String(open)} onClose={() => onOpenChange(false)} {...rest} />
      </DialogContent>
    </Dialog>
  )
}

function ComposerForm({
  task,
  defaultCategory = "practice",
  onChooseComplianceReminder,
  onClose,
}: Omit<TaskComposerProps, "open" | "onOpenChange"> & { onClose: () => void }) {
  const [label, setLabel] = useState(task?.label ?? "")
  const [category, setCategory] = useState<TaskCategory>(task?.category ?? defaultCategory)
  const [dueDate, setDueDate] = useState(task?.due_date ?? "")
  const [repeat, setRepeat] = useState<RepeatRule | "">(task?.repeat_rule ?? "")
  const [notes, setNotes] = useState(task?.notes ?? "")
  const [missingTitle, setMissingTitle] = useState(false)
  const create = useCreateTask()
  const update = useUpdateTask()
  const saving = create.isPending || update.isPending
  const failed = create.isError || update.isError

  async function save() {
    if (!label.trim()) {
      setMissingTitle(true)
      return
    }
    const fields = {
      label: label.trim(),
      category,
      due_date: dueDate || null,
      repeat_rule: repeat || null,
      notes: notes.trim() || null,
    }
    try {
      if (task) {
        // The compliance template a task followed is not this form's to change,
        // unless the task moves into or out of compliance.
        const itemType = category === task.category ? task.item_type : null
        await update.mutateAsync({
          id: task.id,
          payload: { ...taskToPayload(task), ...fields, item_type: itemType },
        })
      } else {
        await create.mutateAsync(fields)
      }
    } catch {
      // The mutation's error state says so below; the dialog stays open.
      return
    }
    onClose()
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle className="font-display">{task ? "Edit task" : "Add a task"}</DialogTitle>
        <DialogDescription>Pablo shows it on your dashboard as it comes due.</DialogDescription>
      </DialogHeader>

      <div className="space-y-4 pt-2">
        <div className="space-y-1">
          <Label htmlFor="task-title">Title</Label>
          <Input
            id="task-title"
            value={label}
            onChange={(e) => {
              setLabel(e.target.value)
              setMissingTitle(false)
            }}
            aria-invalid={missingTitle || undefined}
            aria-describedby={missingTitle ? "task-title-error" : undefined}
            autoFocus
          />
          {missingTitle && (
            <p id="task-title-error" className="text-[11px] text-rose-700">
              Add a title.
            </p>
          )}
        </div>

        <fieldset className="space-y-1">
          <legend className="text-sm font-medium text-neutral-900">Category</legend>
          <div role="radiogroup" aria-label="Category" className="flex flex-wrap gap-1.5">
            {TASK_CATEGORIES.map((c) => (
              <button
                key={c.id}
                type="button"
                role="radio"
                aria-checked={category === c.id}
                onClick={() => setCategory(c.id)}
                className={`rounded-full border px-3 py-1 text-xs font-medium ${
                  category === c.id
                    ? "border-primary-300 bg-primary-50 text-primary-800"
                    : "border-neutral-200 bg-white text-neutral-600"
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
          {category === "compliance" && !task && onChooseComplianceReminder && (
            <p className="text-[11px] text-neutral-500 pt-1">
              Tracking a license, insurance or training?{" "}
              <button
                type="button"
                onClick={onChooseComplianceReminder}
                className="underline text-neutral-700 hover:text-neutral-900"
              >
                Choose it from the list
              </button>
            </p>
          )}
        </fieldset>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="space-y-1">
            <Label htmlFor="task-due">Due date (optional)</Label>
            <Input
              id="task-due"
              type="date"
              value={dueDate}
              onChange={(e) => setDueDate(e.target.value)}
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="task-repeat">Repeats</Label>
            <select
              id="task-repeat"
              value={repeat}
              onChange={(e) => setRepeat(e.target.value as RepeatRule | "")}
              className="h-9 w-full rounded-md border border-neutral-200 bg-white px-2 text-sm"
            >
              <option value="">Does not repeat</option>
              {REPEAT_RULES.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.label}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="space-y-1">
          <Label htmlFor="task-notes">Notes (optional)</Label>
          <Textarea
            id="task-notes"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            rows={2}
          />
        </div>

        {failed && (
          <p className="text-sm text-rose-700" role="alert">
            That didn&apos;t save. Try again.
          </p>
        )}
      </div>

      <div className="flex justify-between gap-2 pt-4 border-t border-neutral-100 mt-2">
        <Button variant="ghost" onClick={onClose} disabled={saving}>
          Cancel
        </Button>
        <Button onClick={() => void save()}>{saving ? "Saving…" : "Save task"}</Button>
      </div>
    </>
  )
}
