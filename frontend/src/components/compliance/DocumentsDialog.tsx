// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Download, FileText, Loader2, Trash2, Upload } from "lucide-react"
import { useRef, useState } from "react"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import {
  useComplianceDocuments,
  useDeleteComplianceDocument,
  useUploadComplianceDocument,
} from "@/hooks/useCompliance"
import { downloadComplianceDocument } from "@/lib/api/compliance"
import {
  COMPLIANCE_DOC_ALLOWED_MIME_TYPES,
  COMPLIANCE_DOC_MAX_BYTES,
  type ComplianceDocument,
  type ComplianceItem,
} from "@/types/compliance"

export interface DocumentsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  item: ComplianceItem
}

/**
 * The credential vault for a single compliance item: list the evidence
 * documents attached to it, upload the certificate that proves it, download
 * a copy, or remove one. Wraps the /api/compliance/{id}/documents endpoints.
 */
export function DocumentsDialog({ open, onOpenChange, item }: DocumentsDialogProps) {
  const { data: documents = [], isLoading } = useComplianceDocuments(item.id, open)
  const upload = useUploadComplianceDocument(item.id)
  const remove = useDeleteComplianceDocument(item.id)

  const fileInputRef = useRef<HTMLInputElement>(null)
  const [error, setError] = useState<string | null>(null)

  function handlePick() {
    setError(null)
    fileInputRef.current?.click()
  }

  function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    // Reset the input so picking the same file again still fires onChange.
    e.target.value = ""
    if (!file) return

    if (!COMPLIANCE_DOC_ALLOWED_MIME_TYPES.includes(file.type as never)) {
      setError("Unsupported file type. Upload a PDF, PNG, or JPEG.")
      return
    }
    if (file.size > COMPLIANCE_DOC_MAX_BYTES) {
      setError("That file is over the 25 MB limit.")
      return
    }

    setError(null)
    // document_type defaults to the item's own type so the vault stays
    // self-describing without asking the clinician to label every upload.
    upload.mutate(
      { file, documentType: item.item_type },
      { onError: (err) => setError(err.message) },
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Documents — {item.label}</DialogTitle>
          <DialogDescription>
            Attach the certificate or confirmation that proves this credential.
            Pablo keeps it on file so it&apos;s ready when you need it.
          </DialogDescription>
        </DialogHeader>

        <input
          ref={fileInputRef}
          type="file"
          accept={COMPLIANCE_DOC_ALLOWED_MIME_TYPES.join(",")}
          onChange={handleFile}
          className="hidden"
          aria-label="Choose a document to upload"
        />

        {isLoading ? (
          <p className="text-sm text-neutral-500 py-6 text-center">Loading…</p>
        ) : documents.length === 0 ? (
          <EmptyState />
        ) : (
          <ul className="space-y-1.5 max-h-72 overflow-y-auto" role="list">
            {documents.map((doc) => (
              <DocumentRow
                key={doc.id}
                doc={doc}
                onDelete={() => remove.mutate(doc.id)}
                deleting={remove.isPending && remove.variables === doc.id}
              />
            ))}
          </ul>
        )}

        {error && (
          <p className="text-sm text-rose-600" role="alert">
            {error}
          </p>
        )}

        <Button onClick={handlePick} disabled={upload.isPending} className="w-full">
          {upload.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <Upload className="h-4 w-4" aria-hidden />
          )}
          {upload.isPending ? "Uploading…" : "Upload document"}
        </Button>
      </DialogContent>
    </Dialog>
  )
}

function DocumentRow({
  doc,
  onDelete,
  deleting,
}: {
  doc: ComplianceDocument
  onDelete: () => void
  deleting: boolean
}) {
  const [downloading, setDownloading] = useState(false)

  async function handleDownload() {
    setDownloading(true)
    try {
      const blob = await downloadComplianceDocument(doc.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = doc.filename
      a.click()
      URL.revokeObjectURL(url)
    } finally {
      setDownloading(false)
    }
  }

  return (
    <li className="flex items-center gap-3 rounded-lg border border-neutral-200/70 bg-white/60 px-3 py-2">
      <FileText className="h-4 w-4 shrink-0 text-neutral-400" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-neutral-900 truncate">
          {doc.filename}
        </p>
        <p className="text-xs text-neutral-500 mt-0.5">{formatSize(doc.size_bytes)}</p>
      </div>
      <Button
        variant="ghost"
        size="sm"
        onClick={handleDownload}
        disabled={downloading}
        aria-label={`Download ${doc.filename}`}
      >
        {downloading ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <Download className="h-4 w-4" aria-hidden />
        )}
      </Button>
      <Button
        variant="ghost"
        size="sm"
        onClick={onDelete}
        disabled={deleting}
        aria-label={`Remove ${doc.filename}`}
      >
        {deleting ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <Trash2 className="h-4 w-4 text-rose-500" aria-hidden />
        )}
      </Button>
    </li>
  )
}

function EmptyState() {
  return (
    <div className="flex flex-col items-center text-center py-6 text-neutral-500">
      <FileText className="h-8 w-8 text-neutral-300" aria-hidden />
      <p className="text-sm mt-2">
        No documents yet. Upload the proof for this credential.
      </p>
    </div>
  )
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
