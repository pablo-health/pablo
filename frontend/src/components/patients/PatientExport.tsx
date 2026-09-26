// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import {
  CheckCircle,
  Download,
  FileArchive,
  FileJson,
  FileText,
  Loader2,
  X,
} from "lucide-react"
import { downloadPatientExport } from "@/lib/api/patients"
import type { PatientExportFormat } from "@/lib/api/patients"
import { saveFile } from "@/lib/saveFile"

interface PatientExportProps {
  patientId: string
  patientName: string
}

type DialogStep = "format" | "confirm" | "exporting" | "complete"

const FORMAT_LABEL: Record<PatientExportFormat, string> = {
  zip: "archive",
  json: "JSON file",
  pdf: "PDF",
}

/** "a, b and c" — the list the confirm sentence is built from. */
function joinList(items: string[]): string {
  return items.length > 1
    ? `${items.slice(0, -1).join(", ")} and ${items[items.length - 1]}`
    : items.join("")
}

/**
 * One sentence saying what the file will hold, built from the choices made.
 *
 * It names only what is going in. The export is a disclosure of health
 * information and the route records it, options included, on the audit
 * trail; that is the safeguard, so the screen does not recite regulation.
 */
export function exportSummary(
  format: PatientExportFormat,
  patientName: string,
  includeTranscripts: boolean,
  includePsychotherapyNotes: boolean,
): string {
  const contents = ["details", "sessions", "notes"]
  if (includeTranscripts) contents.push("session transcripts")
  const yours = includePsychotherapyNotes ? ", plus your psychotherapy notes" : ""
  return `The ${FORMAT_LABEL[format]} will include ${patientName}'s ${joinList(contents)}${yours}.`
}

export function PatientExport({ patientId, patientName }: PatientExportProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [step, setStep] = useState<DialogStep>("format")
  const [selectedFormat, setSelectedFormat] = useState<PatientExportFormat>("zip")
  const [exportFailed, setExportFailed] = useState(false)
  // Both start unchecked, matching the export endpoint's defaults. The right
  // of access does not reach psychotherapy notes (45 CFR 164.524(a)(1)(i)),
  // and transcripts are the rawest part of the chart, so including either is
  // a deliberate choice. The reasoning stays here, not on the screen.
  const [includeTranscripts, setIncludeTranscripts] = useState(false)
  const [includePsychotherapyNotes, setIncludePsychotherapyNotes] =
    useState(false)

  const handleExport = async () => {
    setStep("exporting")
    setExportFailed(false)
    try {
      const file = await downloadPatientExport(patientId, selectedFormat, {
        includeTranscripts,
        includePsychotherapyNotes,
      })
      saveFile(file.blob, file.filename)
      setStep("complete")
    } catch {
      setExportFailed(true)
      setStep("confirm")
    }
  }

  // Every opening starts from the defaults, so a second export never
  // inherits the first one's choices.
  const handleOpen = () => {
    setStep("format")
    setSelectedFormat("zip")
    setExportFailed(false)
    setIncludeTranscripts(false)
    setIncludePsychotherapyNotes(false)
    setIsOpen(true)
  }

  const handleClose = () => setIsOpen(false)

  const formatOption = (
    format: PatientExportFormat,
    Icon: typeof FileJson,
    title: string,
    description: string,
  ) => {
    const selected = selectedFormat === format
    return (
      <button
        onClick={() => setSelectedFormat(format)}
        aria-pressed={selected}
        className={`w-full p-4 border-2 rounded-lg text-left transition-all ${
          selected
            ? "border-primary-500 bg-primary-50"
            : "border-neutral-200 hover:border-neutral-300"
        }`}
      >
        <div className="flex items-start gap-3">
          <Icon
            className={`w-6 h-6 flex-shrink-0 ${
              selected ? "text-primary-600" : "text-neutral-400"
            }`}
          />
          <div>
            <div className="font-semibold text-neutral-900">{title}</div>
            <div className="text-sm text-neutral-600">{description}</div>
          </div>
        </div>
      </button>
    )
  }

  return (
    <>
      <button
        onClick={handleOpen}
        className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary-600 px-3 text-sm font-medium text-white transition-colors hover:bg-primary-700"
      >
        <Download className="w-4 h-4" />
        Export
      </button>

      {isOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="patient-export-title"
            className="bg-card rounded-lg shadow-xl max-w-md w-full"
          >
            {/* Header */}
            <div className="flex items-center justify-between p-6 border-b border-neutral-200">
              <h2
                id="patient-export-title"
                className="text-xl font-display font-bold text-neutral-900"
              >
                Export this chart
              </h2>
              <button
                onClick={handleClose}
                aria-label="Close export"
                className="text-neutral-400 hover:text-neutral-600 transition-colors"
                disabled={step === "exporting"}
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            {/* Content */}
            <div className="p-6">
              {step === "format" && (
                <div className="space-y-4">
                  <div className="space-y-3">
                    {/* The archive is the default: it carries the PDF and
                        the JSON together, with the schema and checksums
                        that let another system trust what it received. */}
                    {formatOption(
                      "zip",
                      FileArchive,
                      "Archive",
                      "A PDF and structured data in one ZIP file",
                    )}
                    {formatOption(
                      "json",
                      FileJson,
                      "JSON",
                      "Structured data another system can read",
                    )}
                    {formatOption(
                      "pdf",
                      FileText,
                      "PDF",
                      "A document to read or print",
                    )}
                  </div>

                  <div className="space-y-2">
                    <label
                      htmlFor="patient-export-include-transcripts"
                      className="flex items-start gap-2 text-sm text-neutral-900"
                    >
                      <input
                        type="checkbox"
                        id="patient-export-include-transcripts"
                        className="mt-0.5 h-4 w-4"
                        checked={includeTranscripts}
                        onChange={(e) => setIncludeTranscripts(e.target.checked)}
                      />
                      <span>Include session transcripts</span>
                    </label>
                    <label
                      htmlFor="patient-export-include-psychotherapy-notes"
                      className="flex items-start gap-2 text-sm text-neutral-900"
                    >
                      <input
                        type="checkbox"
                        id="patient-export-include-psychotherapy-notes"
                        className="mt-0.5 h-4 w-4"
                        checked={includePsychotherapyNotes}
                        onChange={(e) =>
                          setIncludePsychotherapyNotes(e.target.checked)
                        }
                      />
                      <span>Include psychotherapy notes</span>
                    </label>
                  </div>
                </div>
              )}

              {step === "confirm" && (
                <div className="space-y-3">
                  <p className="text-sm text-neutral-900">
                    {exportSummary(
                      selectedFormat,
                      patientName,
                      includeTranscripts,
                      includePsychotherapyNotes,
                    )}
                  </p>
                  {exportFailed && (
                    <p role="alert" className="text-sm text-red-600">
                      The export didn&apos;t download. Try again.
                    </p>
                  )}
                </div>
              )}

              {step === "exporting" && (
                <div className="flex flex-col items-center justify-center gap-3 py-8">
                  <Loader2 className="w-12 h-12 text-primary-600 animate-spin" />
                  <p className="text-sm text-neutral-600">Preparing the file…</p>
                </div>
              )}

              {step === "complete" && (
                <div className="flex flex-col items-center justify-center py-8">
                  <CheckCircle className="w-16 h-16 text-secondary-600 mb-4" />
                  <p className="text-sm text-neutral-600 text-center">
                    Your download has started.
                  </p>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="flex items-center justify-end gap-3 p-6 border-t border-neutral-200">
              {step === "format" && (
                <>
                  <button onClick={handleClose} className="btn-secondary">
                    Cancel
                  </button>
                  <button
                    onClick={() => setStep("confirm")}
                    className="btn-primary"
                  >
                    Continue
                  </button>
                </>
              )}

              {step === "confirm" && (
                <>
                  <button
                    onClick={() => setStep("format")}
                    className="btn-secondary"
                  >
                    Back
                  </button>
                  <button onClick={handleExport} className="btn-primary">
                    Download
                  </button>
                </>
              )}

              {step === "complete" && (
                <button onClick={handleClose} className="btn-primary">
                  Close
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
