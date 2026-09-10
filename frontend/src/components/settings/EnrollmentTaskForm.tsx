// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * EnrollmentTaskForm
 *
 * What a payer's PROVIDER_ACTION_REQUIRED enrollment task renders as: the
 * clearinghouse's own instructions and links, a text input per TEXT field
 * and a PDF picker per DOCUMENT field. Submitting uploads any documents,
 * waits for them to clear, and completes the task — the payer sees it
 * without the therapist ever leaving Pablo or holding a clearinghouse login.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useCompleteEnrollmentTask, useEnrollmentDetail } from "@/hooks/useCoverage"
import { resolveEnrollmentTaskLink } from "@/lib/api/coverage"
import type { EnrollmentTask, EnrollmentTaskLink, EnrollmentTransactionType } from "@/types/coverage"

function TaskLink({
  link,
  payerRowId,
  transactionType,
  taskId,
  linkIndex,
}: {
  link: EnrollmentTaskLink
  payerRowId: string
  transactionType: EnrollmentTransactionType
  taskId: string
  linkIndex: number
}) {
  const [resolving, setResolving] = useState(false)

  if (link.kind === "external") {
    return (
      <a
        href={link.url}
        target="_blank"
        rel="noreferrer"
        className="text-sm text-primary underline"
      >
        {link.label}
      </a>
    )
  }

  // A Stedi-hosted link 401s if opened directly — hop one resolves it to a
  // pre-signed URL the browser can fetch unauthenticated.
  async function open() {
    setResolving(true)
    try {
      const { url } = await resolveEnrollmentTaskLink(
        payerRowId,
        transactionType,
        taskId,
        linkIndex,
      )
      window.open(url, "_blank", "noreferrer")
    } finally {
      setResolving(false)
    }
  }

  return (
    <button
      type="button"
      onClick={() => void open()}
      disabled={resolving}
      className="text-sm text-primary underline disabled:opacity-60"
    >
      {link.label}
    </button>
  )
}

function TaskForm({
  payerRowId,
  transactionType,
  task,
}: {
  payerRowId: string
  transactionType: EnrollmentTransactionType
  task: EnrollmentTask
}) {
  const complete = useCompleteEnrollmentTask()
  const [text, setText] = useState<Record<string, string>>({})
  const [files, setFiles] = useState<Record<string, File>>({})

  const canSubmit = task.fields.every((field) =>
    field.field_type === "DOCUMENT"
      ? !!files[field.key]
      : (text[field.key] ?? "").trim().length > 0,
  )

  function submit() {
    const fieldValues: Record<string, string | File> = { ...text, ...files }
    complete.mutate({ payerRowId, transactionType, taskId: task.id, fieldValues })
  }

  const error = complete.error instanceof Error ? complete.error.message : null

  return (
    <div className="mt-2 space-y-3 rounded-md border border-border p-3">
      {task.instructions && (
        <p className="whitespace-pre-line text-sm text-foreground">{task.instructions}</p>
      )}
      {task.links.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {task.links.map((link, index) => (
            <TaskLink
              key={link.url}
              link={link}
              payerRowId={payerRowId}
              transactionType={transactionType}
              taskId={task.id}
              linkIndex={index}
            />
          ))}
        </div>
      )}
      {task.fields.map((field) => (
        <div key={field.key} className="grid gap-1.5">
          <Label htmlFor={`task-field-${task.id}-${field.key}`}>{field.label}</Label>
          {field.description && (
            <p className="text-[12.5px] text-muted-foreground">{field.description}</p>
          )}
          {field.field_type === "TEXT" ? (
            <Input
              id={`task-field-${task.id}-${field.key}`}
              value={text[field.key] ?? ""}
              onChange={(e) => setText((prev) => ({ ...prev, [field.key]: e.target.value }))}
            />
          ) : (
            <input
              id={`task-field-${task.id}-${field.key}`}
              type="file"
              accept="application/pdf"
              onChange={(e) => {
                const file = e.target.files?.[0]
                if (file) setFiles((prev) => ({ ...prev, [field.key]: file }))
              }}
            />
          )}
        </div>
      ))}
      {error && <p className="text-[12.5px] text-destructive">{error}</p>}
      <Button
        type="button"
        size="sm"
        onClick={submit}
        disabled={complete.isPending || (task.fields.length > 0 && !canSubmit)}
      >
        {complete.isPending ? "Submitting…" : "Mark complete"}
      </Button>
    </div>
  )
}

/** Every open PROVIDER task on this enrollment, rendered as a form. Nothing
 * to show once every task is complete or STEDI's own. */
export function EnrollmentTaskPanel({
  payerRowId,
  transactionType,
}: {
  payerRowId: string
  transactionType: EnrollmentTransactionType
}) {
  const { data, isLoading } = useEnrollmentDetail(payerRowId, transactionType)
  const openTasks = (data?.tasks ?? []).filter(
    (task) => task.responsible_party === "PROVIDER" && !task.is_complete,
  )

  if (isLoading) return <p className="text-[12.5px] text-muted-foreground">Loading…</p>
  if (openTasks.length === 0) return null

  return (
    <div className="space-y-2">
      {openTasks.map((task) => (
        <TaskForm
          key={task.id}
          payerRowId={payerRowId}
          transactionType={transactionType}
          task={task}
        />
      ))}
    </div>
  )
}
