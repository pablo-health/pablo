// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { ChangeEvent, useRef, useState } from "react"
import {
  AlertCircle,
  Download,
  FileText,
  Loader2,
  Lock,
  Trash2,
  Upload,
} from "lucide-react"

import { ApiError } from "@/lib/api/client"
import { buildPatientDocumentDownloadUrl } from "@/lib/api/patientDocuments"
import {
  useDeletePatientDocument,
  usePatientDocuments,
  useUploadPatientDocument,
} from "@/hooks/usePatientDocuments"
import {
  ALLOWED_DOCUMENT_MIME_TYPES,
  type DocumentCategory,
  type PatientDocumentResponse,
} from "@/types/patientDocuments"

const CATEGORY_OPTIONS: { value: DocumentCategory; label: string; hint: string }[] = [
  {
    value: "chart",
    label: "Patient chart",
    hint: "Shared with co-treating clinicians. Releasable to the patient via standard records request.",
  },
  {
    value: "therapist_private",
    label: "Therapist private",
    hint: "Only visible to you. Working material kept out of the patient record.",
  },
  {
    value: "psychotherapy_notes",
    label: "Psychotherapy notes",
    hint: "Only visible to you. HIPAA §164.501 — requires separate authorization to release; not subject to patient right-of-access.",
  },
]

function categoryBadge(category: DocumentCategory): string | null {
  if (category === "therapist_private") return "therapist private"
  if (category === "psychotherapy_notes") return "psychotherapy notes"
  return null
}

/**
 * Patient document upload + list (THERAPY-ak6m.2).
 *
 * Scope per the ak6m.2 bead: single-file upload via signed URL, list,
 * download, delete. Drag-and-drop and preview thumbnails are explicitly
 * deferred to ak6m.2.1 — keep this component minimal so the polish
 * pass has a clean baseline to build on.
 */

interface PatientDocumentsProps {
  patientId: string
}

const ACCEPT = ALLOWED_DOCUMENT_MIME_TYPES.join(",")

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}

export function PatientDocuments({ patientId }: PatientDocumentsProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [category, setCategory] = useState<DocumentCategory>("chart")

  const { data, isLoading, error: listError } = usePatientDocuments(patientId)
  const upload = useUploadPatientDocument(patientId)
  const remove = useDeletePatientDocument(patientId)

  const documents = data?.data ?? []

  const handleFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) return
    setUploadError(null)
    upload.mutate(
      { file, category },
      {
        onError: (err) => {
          if (err instanceof ApiError) {
            setUploadError(err.message)
          } else if (err instanceof Error) {
            setUploadError(err.message)
          } else {
            setUploadError("Upload failed")
          }
        },
        onSettled: () => {
          if (fileInputRef.current) {
            fileInputRef.current.value = ""
          }
        },
      },
    )
  }

  const handleDelete = (doc: PatientDocumentResponse) => {
    if (!confirm(`Delete ${doc.filename}? This can't be undone yet (undo support lands in ak6m.2.1).`)) {
      return
    }
    remove.mutate({ documentId: doc.id })
  }

  const stageLabel = (() => {
    switch (upload.stage) {
      case "init":
        return "Preparing upload…"
      case "uploading":
        return "Uploading to storage…"
      case "finalize":
        return "Extracting text…"
      default:
        return null
    }
  })()

  return (
    <div className="card">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-xl font-display font-bold text-neutral-900">
            Documents
          </h2>
          <p className="text-sm text-neutral-500">
            Upload PDFs, PNGs, or JPEGs to attach to this patient&apos;s chart.
          </p>
        </div>
        <div className="flex items-end gap-3">
          <label className="inline-flex flex-col gap-0.5 text-sm text-neutral-600">
            <span className="text-xs text-neutral-500">Visibility</span>
            <select
              value={category}
              onChange={(e) => setCategory(e.target.value as DocumentCategory)}
              disabled={upload.isPending}
              className="rounded border border-neutral-300 px-2 py-1 text-sm"
              data-testid="patient-document-category-select"
              title={
                CATEGORY_OPTIONS.find((o) => o.value === category)?.hint ?? ""
              }
            >
              {CATEGORY_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
          </label>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT}
            onChange={handleFile}
            disabled={upload.isPending}
            className="hidden"
            data-testid="patient-document-file-input"
          />
          <button
            type="button"
            className="btn-primary inline-flex items-center gap-2"
            onClick={() => fileInputRef.current?.click()}
            disabled={upload.isPending}
          >
            {upload.isPending ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Upload className="w-4 h-4" />
            )}
            <span>Upload document</span>
          </button>
        </div>
      </div>

      {stageLabel && (
        <p className="text-sm text-neutral-500 mb-3">{stageLabel}</p>
      )}

      {uploadError && (
        <div className="mb-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {uploadError}
        </div>
      )}

      {isLoading ? (
        <p className="text-neutral-500 text-sm">Loading documents…</p>
      ) : listError ? (
        <p className="text-red-500 text-sm">Failed to load documents.</p>
      ) : documents.length === 0 ? (
        <p className="text-neutral-500 text-sm">
          No documents uploaded yet.
        </p>
      ) : (
        <ul className="divide-y divide-neutral-100">
          {documents.map((doc) => (
            <li
              key={doc.id}
              className="flex items-center justify-between py-3"
            >
              <div className="flex items-start gap-3 min-w-0">
                <FileText className="w-5 h-5 text-neutral-500 flex-shrink-0 mt-0.5" />
                <div className="min-w-0">
                  <p className="font-medium text-neutral-900 truncate inline-flex items-center gap-1.5">
                    {doc.filename}
                    {doc.category !== "chart" && (
                      <Lock
                        className="w-3.5 h-3.5 text-neutral-500"
                        aria-label={
                          doc.category === "psychotherapy_notes"
                            ? "Psychotherapy notes (only you can see this)"
                            : "Therapist private (only you can see this)"
                        }
                      />
                    )}
                  </p>
                  <p className="text-xs text-neutral-500">
                    {formatBytes(doc.size_bytes)} · uploaded{" "}
                    {formatDate(doc.created_at)}
                    {categoryBadge(doc.category) && ` · ${categoryBadge(doc.category)}`}
                  </p>
                  {doc.text_extraction_failed && (
                    <span className="mt-1 inline-flex items-center gap-1 rounded-full bg-yellow-100 px-2 py-0.5 text-xs text-yellow-700">
                      <AlertCircle className="w-3 h-3" />
                      OCR not yet supported — text extraction failed
                    </span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <a
                  href={buildPatientDocumentDownloadUrl(doc.id)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="btn-secondary inline-flex items-center gap-1 text-sm"
                >
                  <Download className="w-4 h-4" />
                  Download
                </a>
                <button
                  type="button"
                  className="btn-secondary inline-flex items-center gap-1 text-sm text-red-600 hover:text-red-700"
                  onClick={() => handleDelete(doc)}
                  disabled={
                    remove.isPending && remove.variables?.documentId === doc.id
                  }
                >
                  <Trash2 className="w-4 h-4" />
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
