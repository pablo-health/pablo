// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * EnrollmentTaskForm
 *
 * What the payer is waiting for, as something to fill in rather than
 * something to read. An enrollment stops the moment the payer wants a signed
 * agreement or a Medicaid id, and until now the only thing this screen could
 * do was print the request and send the practice to the clearinghouse's own
 * portal, holding a login it does not have.
 *
 * A task is a form. Some ask for text, some for a PDF, some for both, and
 * some ask for nothing at all — those are an instruction to go and do
 * something elsewhere, and the button asserts it was done.
 *
 * Every field is required: the clearinghouse takes an answer whole or not at
 * all, so the button stays disabled rather than sending half of one.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useAnswerEnrollmentTask, useEnrollmentTasks } from "@/hooks/useCoverage"
import { getEnrollmentDocumentUrl } from "@/lib/api/coverage"
import type {
  EnrollmentDocumentResponse,
  EnrollmentTaskResponse,
  EnrollmentTransactionType,
} from "@/types/coverage"

export const NOTHING_OUTSTANDING = "Nothing outstanding — the payer has what it needs."

interface Props {
  payerRowId: string
  transactionType: EnrollmentTransactionType
}

function DocumentRow({
  payerRowId,
  transactionType,
  document: doc,
}: Props & { document: EnrollmentDocumentResponse }) {
  const [failed, setFailed] = useState(false)

  async function open() {
    setFailed(false)
    try {
      const { url } = await getEnrollmentDocumentUrl(payerRowId, transactionType, doc.id)
      window.open(url, "_blank", "noopener,noreferrer")
    } catch {
      setFailed(true)
    }
  }

  return (
    <li className="flex items-baseline justify-between gap-3 py-1 text-[12.5px]">
      <span className="text-muted-foreground">{doc.name ?? "Document"}</span>
      {doc.status === "UPLOADED" ? (
        <button
          type="button"
          className="text-foreground underline underline-offset-2"
          data-testid={`enrollment-document-${doc.id}`}
          onClick={open}
        >
          {failed ? "Try again" : "Open"}
        </button>
      ) : (
        <span className="text-muted-foreground">
          {doc.status === "PENDING" ? "Uploading…" : "Could not be read"}
        </span>
      )}
    </li>
  )
}

function TaskForm({
  payerRowId,
  transactionType,
  task,
}: Props & { task: EnrollmentTaskResponse }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [documents, setDocuments] = useState<Record<string, File>>({})
  const answer = useAnswerEnrollmentTask()

  const answered = task.fields.every((field) =>
    field.field_type === "DOCUMENT" ? !!documents[field.key] : !!values[field.key]?.trim(),
  )
  const error = answer.error instanceof Error ? answer.error.message : null

  return (
    <li className="space-y-2 py-2" data-testid={`enrollment-task-${task.id}`}>
      {task.instructions && (
        <p className="whitespace-pre-line text-[12.5px] text-foreground">{task.instructions}</p>
      )}
      {task.links.map((link) => (
        <a
          key={link.url}
          href={link.url}
          target="_blank"
          rel="noopener noreferrer"
          className="block text-[12.5px] text-foreground underline underline-offset-2"
        >
          {link.label}
        </a>
      ))}
      {task.fields.map((field) => (
        <div key={field.key} className="space-y-1">
          <Label htmlFor={`${task.id}-${field.key}`} className="text-[12.5px]">
            {field.label}
          </Label>
          {field.field_type === "DOCUMENT" ? (
            <Input
              id={`${task.id}-${field.key}`}
              type="file"
              accept="application/pdf"
              data-testid={`enrollment-field-${field.key}`}
              onChange={(event) => {
                const file = event.target.files?.[0]
                setDocuments((current) =>
                  file ? { ...current, [field.key]: file } : current,
                )
              }}
            />
          ) : (
            <Input
              id={`${task.id}-${field.key}`}
              data-testid={`enrollment-field-${field.key}`}
              value={values[field.key] ?? ""}
              onChange={(event) =>
                setValues((current) => ({ ...current, [field.key]: event.target.value }))
              }
            />
          )}
          {field.description && (
            <p className="text-[12px] text-muted-foreground">{field.description}</p>
          )}
        </div>
      ))}
      <Button
        type="button"
        size="sm"
        data-testid={`enrollment-task-submit-${task.id}`}
        disabled={!answered || answer.isPending}
        onClick={() =>
          answer.mutate({
            payerRowId,
            transactionType,
            taskId: task.id,
            values,
            documents,
          })
        }
      >
        {task.fields.length === 0 ? "Mark as done" : "Send to the payer"}
      </Button>
      {error && <p className="text-[12.5px] text-destructive">{error}</p>}
    </li>
  )
}

export function EnrollmentTaskForm({ payerRowId, transactionType }: Props) {
  const { data, isLoading, error } = useEnrollmentTasks(payerRowId, transactionType)

  if (isLoading) {
    return <p className="text-[12.5px] text-muted-foreground">Checking with the clearinghouse…</p>
  }
  if (error) {
    return (
      <p className="text-[12.5px] text-destructive">
        {error instanceof Error ? error.message : "The clearinghouse is not answering."}
      </p>
    )
  }

  const tasks = data?.data ?? []
  const documents = data?.documents ?? []

  return (
    <div className="mt-1 space-y-2" data-testid="enrollment-tasks">
      {tasks.length === 0 ? (
        <p className="text-[12.5px] text-muted-foreground">{NOTHING_OUTSTANDING}</p>
      ) : (
        <ul className="m-0 list-none divide-y divide-border p-0">
          {tasks.map((task) => (
            <TaskForm
              key={task.id}
              payerRowId={payerRowId}
              transactionType={transactionType}
              task={task}
            />
          ))}
        </ul>
      )}
      {documents.length > 0 && (
        <ul className="m-0 list-none p-0">
          {documents.map((doc) => (
            <DocumentRow
              key={doc.id}
              payerRowId={payerRowId}
              transactionType={transactionType}
              document={doc}
            />
          ))}
        </ul>
      )}
    </div>
  )
}
