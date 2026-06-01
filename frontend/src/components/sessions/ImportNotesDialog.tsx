// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ImportNotesDialog
 *
 * Bring an existing patient's documented history into Pablo. The clinician
 * drops one file or a whole chart's worth of prior SOAP notes (PDF/Word/TXT);
 * each is read, parsed into a structured note dated from the document, and
 * filed as a session awaiting review. Files import a few at a time with a
 * per-file progress list, so one unreadable file never blocks the rest.
 */

"use client"

import { useCallback, useMemo, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { format } from "date-fns"
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  FileText,
  Loader2,
  Upload,
  X,
} from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useImportNotes, type ImportItem } from "@/hooks/useImportNotes"
import { formatFileSize, getFileExtension } from "@/lib/utils/fileValidation"

const ACCEPTED_EXTENSIONS = [".pdf", ".docx", ".txt"] as const
const MAX_BYTES = 15 * 1024 * 1024

export interface ImportNotesDialogProps {
  patientId: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

function fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`
}

export function ImportNotesDialog({
  patientId,
  open,
  onOpenChange,
}: ImportNotesDialogProps) {
  const router = useRouter()
  const { items, isRunning, isComplete, doneCount, errorCount, start, reset } =
    useImportNotes(patientId)

  const [selected, setSelected] = useState<File[]>([])
  const [rejected, setRejected] = useState<string[]>([])
  const [isDragging, setIsDragging] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const started = items.length > 0

  const addFiles = useCallback((incoming: FileList | File[]) => {
    const accepted: File[] = []
    const refused: string[] = []
    for (const file of Array.from(incoming)) {
      const ext = getFileExtension(file.name).toLowerCase()
      if (!ACCEPTED_EXTENSIONS.includes(ext as (typeof ACCEPTED_EXTENSIONS)[number])) {
        refused.push(`${file.name} — only PDF, Word, or TXT`)
      } else if (file.size > MAX_BYTES) {
        refused.push(`${file.name} — over 15 MB`)
      } else {
        accepted.push(file)
      }
    }
    setRejected(refused)
    setSelected((prev) => {
      const seen = new Set(prev.map(fileKey))
      return [...prev, ...accepted.filter((f) => !seen.has(fileKey(f)))]
    })
  }, [])

  const removeSelected = useCallback((key: string) => {
    setSelected((prev) => prev.filter((f) => fileKey(f) !== key))
  }, [])

  const resetAll = useCallback(() => {
    setSelected([])
    setRejected([])
    setIsDragging(false)
    reset()
  }, [reset])

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) resetAll()
      onOpenChange(next)
    },
    [onOpenChange, resetAll],
  )

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
    if (e.dataTransfer.files?.length) addFiles(e.dataTransfer.files)
  }

  const goToReview = useCallback(() => {
    handleOpenChange(false)
    router.push("/dashboard/sessions")
  }, [handleOpenChange, router])

  const summary = useMemo(() => {
    if (!started) return null
    if (isRunning) return `Reading ${items.length} note${items.length === 1 ? "" : "s"}…`
    const parts = [`${doneCount} added`]
    if (errorCount > 0) parts.push(`${errorCount} need${errorCount === 1 ? "s" : ""} attention`)
    return parts.join(" · ")
  }, [started, isRunning, items.length, doneCount, errorCount])

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[640px]">
        <DialogHeader>
          <DialogTitle>Import existing notes</DialogTitle>
          <DialogDescription>
            Upload prior SOAP notes (PDF, Word, or TXT). Pablo reads each one, pulls out
            the date and the S/O/A/P sections, and files it against this patient
            for your review. Drop a whole chart&apos;s worth at once.
          </DialogDescription>
        </DialogHeader>

        {!started ? (
          <div className="space-y-4">
            {/* Drop zone */}
            <div
              onDragEnter={(e) => {
                e.preventDefault()
                setIsDragging(true)
              }}
              onDragOver={(e) => e.preventDefault()}
              onDragLeave={(e) => {
                e.preventDefault()
                setIsDragging(false)
              }}
              onDrop={handleDrop}
              className={cn(
                "relative rounded-xl border-2 border-dashed p-8 text-center transition-colors",
                isDragging
                  ? "border-primary bg-primary-50"
                  : "border-neutral-300 hover:border-neutral-400",
              )}
            >
              <input
                ref={inputRef}
                type="file"
                accept=".pdf,.docx,.txt"
                multiple
                onChange={(e) => {
                  if (e.target.files?.length) addFiles(e.target.files)
                  e.target.value = ""
                }}
                className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                aria-label="Choose note files to import"
              />
              <Upload
                className={cn(
                  "mx-auto mb-3 h-10 w-10",
                  isDragging ? "text-primary-600" : "text-neutral-400",
                )}
              />
              <p className="text-sm font-medium text-neutral-700">
                {isDragging ? "Drop the files here" : "Drag & drop files, or click to browse"}
              </p>
              <p className="mt-1 text-xs text-neutral-500">
                PDF, Word, or TXT, up to 15 MB each — select as many as you like
              </p>
            </div>

            {rejected.length > 0 && (
              <ul className="space-y-1">
                {rejected.map((r) => (
                  <li
                    key={r}
                    className="flex items-center gap-2 text-xs text-destructive"
                  >
                    <AlertCircle className="h-3.5 w-3.5 shrink-0" />
                    <span>{r}</span>
                  </li>
                ))}
              </ul>
            )}

            {selected.length > 0 && (
              <ul className="divide-y divide-neutral-100 rounded-lg border border-neutral-200">
                {selected.map((file) => (
                  <li
                    key={fileKey(file)}
                    className="flex items-center gap-3 px-3 py-2.5"
                  >
                    <FileText className="h-5 w-5 shrink-0 text-primary-600" />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium text-neutral-900">
                        {file.name}
                      </p>
                      <p className="text-xs text-neutral-500">
                        {formatFileSize(file.size)}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => removeSelected(fileKey(file))}
                      aria-label={`Remove ${file.name}`}
                      className="rounded-md p-1 text-neutral-400 transition-colors hover:bg-neutral-100 hover:text-neutral-700"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <p
              className="text-sm font-medium text-neutral-700"
              role="status"
              aria-live="polite"
            >
              {summary}
            </p>
            <ul className="max-h-[320px] space-y-1 overflow-y-auto">
              {items.map((item) => (
                <ImportRow key={item.id} item={item} />
              ))}
            </ul>
          </div>
        )}

        <DialogFooter>
          {isComplete ? (
            <>
              <Button variant="outline" onClick={() => handleOpenChange(false)}>
                Close
              </Button>
              {doneCount > 0 && (
                <Button onClick={goToReview}>
                  Go to Review
                  <ArrowRight className="ml-2 h-4 w-4" />
                </Button>
              )}
            </>
          ) : (
            <>
              <Button
                variant="outline"
                onClick={() => handleOpenChange(false)}
                disabled={isRunning}
              >
                Cancel
              </Button>
              <Button
                onClick={() => start(selected)}
                disabled={isRunning || selected.length === 0}
              >
                {isRunning ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    Importing…
                  </>
                ) : (
                  `Import ${selected.length || ""} ${
                    selected.length === 1 ? "note" : "notes"
                  }`.trim()
                )}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ImportRow({ item }: { item: ImportItem }) {
  return (
    <li className="flex items-center gap-3 rounded-lg px-3 py-2">
      <StatusIcon status={item.status} />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm font-medium text-neutral-900">
          {item.file.name}
        </p>
        <p
          className={cn(
            "truncate text-xs",
            item.status === "error" ? "text-destructive" : "text-neutral-500",
          )}
        >
          {rowDetail(item)}
        </p>
      </div>
    </li>
  )
}

function StatusIcon({ status }: { status: ImportItem["status"] }) {
  if (status === "done") {
    return <CheckCircle2 className="h-5 w-5 shrink-0 text-green-600" aria-hidden />
  }
  if (status === "error") {
    return <AlertCircle className="h-5 w-5 shrink-0 text-destructive" aria-hidden />
  }
  if (status === "parsing") {
    return <Loader2 className="h-5 w-5 shrink-0 animate-spin text-primary-600" aria-hidden />
  }
  return (
    <span
      className="h-5 w-5 shrink-0 rounded-full border-2 border-neutral-200"
      aria-hidden
    />
  )
}

function rowDetail(item: ImportItem): string {
  switch (item.status) {
    case "queued":
      return "Waiting…"
    case "parsing":
      return "Reading and parsing…"
    case "error":
      return item.error ?? "Couldn't import this file."
    case "done": {
      const date = item.session?.session_date
      return date ? `Added · ${format(new Date(date), "MMM d, yyyy")}` : "Added"
    }
  }
}
