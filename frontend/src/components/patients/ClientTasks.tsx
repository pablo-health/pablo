// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The clinician's open tasks about this client, on the client's chart, with
 * a way to add one that starts tied to them. These are the clinician's own
 * tasks: a co-treating clinician sees only theirs. Done ones drop off here and
 * stay on the Tasks page.
 */

"use client"

import { useMemo, useState } from "react"
import { ComplianceItemRow } from "@/components/compliance/ComplianceItemRow"
import { enrichItems, sortByDueDate } from "@/components/compliance/horizons"
import { TaskComposer } from "@/components/tasks/TaskComposer"
import { Button } from "@/components/ui/button"
import { useCompleteTask, useTasks } from "@/hooks/useTasks"
import type { Task } from "@/types/tasks"

const NO_TEMPLATES = new Map()

export function ClientTasks({
  patientId,
  patientName,
}: {
  patientId: string
  patientName: string
}) {
  const { data } = useTasks({ patient_id: patientId, open_only: true })
  const completeTask = useCompleteTask()
  // `undefined` while closed; `null` for a new task.
  const [editing, setEditing] = useState<Task | null | undefined>(undefined)
  const entries = useMemo(
    () => enrichItems(data ?? [], NO_TEMPLATES).sort(sortByDueDate),
    [data],
  )

  return (
    <section aria-label="Tasks" className="card" data-testid="client-tasks">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-display font-semibold text-neutral-900">Tasks</h2>
        <Button variant="outline" size="sm" onClick={() => setEditing(null)}>
          Add a task
        </Button>
      </div>
      {entries.length > 0 && (
        <ul className="mt-3 space-y-1.5" role="list">
          {entries.map((entry) => (
            <ComplianceItemRow
              key={entry.item.id}
              entry={entry}
              showClient={false}
              onEdit={() => setEditing(entry.item)}
              onComplete={() => completeTask.mutate(entry.item.id)}
              completing={completeTask.isPending}
            />
          ))}
        </ul>
      )}
      <TaskComposer
        open={editing !== undefined}
        onOpenChange={(open) => !open && setEditing(undefined)}
        task={editing ?? null}
        defaultClient={{ id: patientId, name: patientName }}
      />
    </section>
  )
}
