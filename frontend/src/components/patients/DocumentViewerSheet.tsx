// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useEffect, useState } from "react"
import { AlertCircle, Loader2 } from "lucide-react"

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ApiError } from "@/lib/api/client"
import { getPatientDocumentDownloadUrl } from "@/lib/api/patientDocuments"
import type { PatientDocumentResponse } from "@/types/patientDocuments"

interface DocumentViewerSheetProps {
  document: PatientDocumentResponse | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

/**
 * In-app document viewer (PABLO-6x5.3).
 *
 * Right-side drawer that fetches an `inline`-disposition signed URL via the
 * authenticated client (the PABLO-47h contract — the same fetch fires the
 * document-access audit server-side) and renders it in place: native
 * <embed> for PDFs, <img> fit-to-width for images. No PDF.js, no zoom/pan.
 */
export function DocumentViewerSheet({
  document,
  open,
  onOpenChange,
}: DocumentViewerSheetProps) {
  // Keyed by document id so a result from a previously-viewed document is
  // ignored when the drawer is reopened on another. setState lives only in
  // the async callbacks — never synchronously in the effect body.
  const [result, setResult] = useState<{
    id: string
    url?: string
    error?: string
  } | null>(null)

  useEffect(() => {
    if (!open || !document) return
    let cancelled = false
    const id = document.id
    getPatientDocumentDownloadUrl(id, undefined, "inline")
      .then((signed) => {
        if (!cancelled) setResult({ id, url: signed })
      })
      .catch((err) => {
        if (cancelled) return
        setResult({
          id,
          error:
            err instanceof ApiError || err instanceof Error
              ? err.message
              : "Failed to load document",
        })
      })
    return () => {
      cancelled = true
    }
  }, [open, document])

  const ready = result !== null && result.id === document?.id
  const loading = open && !!document && !ready
  const url = ready ? result.url : undefined
  const error = ready ? result.error : undefined
  const isPdf = document?.mime_type === "application/pdf"
  const isImage = document?.mime_type.startsWith("image/") ?? false

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="left-auto right-0 top-0 h-full max-h-screen w-[90vw] max-w-3xl translate-x-0 translate-y-0 flex-col gap-0 rounded-none border-l p-0 sm:max-w-3xl data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right"
      >
        <DialogHeader className="border-b border-neutral-200 px-6 py-4 text-left">
          <DialogTitle className="truncate font-display text-lg font-bold text-neutral-900">
            {document?.filename ?? "Document"}
          </DialogTitle>
          <DialogDescription className="text-sm text-neutral-500">
            {document ? document.mime_type : "Loading…"}
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 overflow-auto bg-neutral-100 p-4">
          {loading && (
            <div className="flex h-full items-center justify-center text-neutral-500">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Loading document…
            </div>
          )}

          {!loading && error && (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-red-600">
              <AlertCircle className="h-6 w-6" />
              <p className="text-sm">{error}</p>
            </div>
          )}

          {!loading && !error && url && isPdf && (
            <embed
              src={url}
              type="application/pdf"
              className="h-full w-full rounded border border-neutral-200 bg-white"
            />
          )}

          {!loading && !error && url && isImage && (
            // eslint-disable-next-line @next/next/no-img-element -- short-lived signed cross-origin GCS URL; next/image can't optimize it
            <img
              src={url}
              alt={document?.filename ?? "Document"}
              className="mx-auto max-w-full rounded border border-neutral-200 bg-white"
            />
          )}

          {!loading && !error && url && !isPdf && !isImage && (
            <div className="flex h-full items-center justify-center text-center text-sm text-neutral-500">
              Preview not available for this file type.
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
