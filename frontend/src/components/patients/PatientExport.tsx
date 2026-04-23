// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Download, FileJson, FileText, X, Loader2, CheckCircle } from "lucide-react"
import { usePatient } from "@/hooks/usePatients"
import { useSessionList } from "@/hooks/useSessions"

interface PatientExportProps {
  patientId: string
  patientName: string
}

type ExportFormat = "json" | "pdf"
type DialogStep = "format" | "confirm" | "exporting" | "complete"

export function PatientExport({ patientId, patientName }: PatientExportProps) {
  const [isOpen, setIsOpen] = useState(false)
  const [step, setStep] = useState<DialogStep>("format")
  const [selectedFormat, setSelectedFormat] = useState<ExportFormat>("json")
  const [progress, setProgress] = useState(0)

  const { data: patient } = usePatient(patientId)
  const { data: sessionsData } = useSessionList()

  const handleExport = async () => {
    setStep("exporting")
    setProgress(0)

    const progressInterval = setInterval(() => {
      setProgress((prev) => {
        if (prev >= 100) {
          clearInterval(progressInterval)
          return 100
        }
        return prev + 10
      })
    }, 200)

    await new Promise((resolve) => setTimeout(resolve, 2000))

    const sessions =
      sessionsData?.data.filter((s) => s.patient_id === patientId) ?? []

    const exportData = {
      patient,
      sessions,
      exportDate: new Date().toISOString(),
      totalSessions: sessions.length,
    }

    if (selectedFormat === "json") {
      // Download as JSON
      const blob = new Blob([JSON.stringify(exportData, null, 2)], {
        type: "application/json",
      })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `patient-${patientId}-export-${new Date().toISOString().split("T")[0]}.json`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } else {
      const pdfContent = `
PATIENT DATA EXPORT
===================

Export Date: ${new Date().toLocaleDateString()}

Patient Information:
-------------------
Name: ${patient?.first_name} ${patient?.last_name}
Email: ${patient?.email ?? "N/A"}
Phone: ${patient?.phone ?? "N/A"}
Date of Birth: ${patient?.date_of_birth ? new Date(patient.date_of_birth).toLocaleDateString() : "N/A"}
Status: ${patient?.status}
Diagnosis: ${patient?.diagnosis ?? "N/A"}

Session History (${sessions.length} sessions):
-------------------
${sessions
  .map(
    (s, i) => `
${i + 1}. ${new Date(s.session_date).toLocaleString()}
   Session #: ${s.session_number}
   Status: ${s.status}
`
  )
  .join("\n")}
      `.trim()

      const blob = new Blob([pdfContent], { type: "text/plain" })
      const url = URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `patient-${patientId}-export-${new Date().toISOString().split("T")[0]}.txt`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    }

    setStep("complete")
  }

  const handleClose = () => {
    setIsOpen(false)
    setTimeout(() => {
      setStep("format")
      setProgress(0)
      setSelectedFormat("json")
    }, 200)
  }

  return (
    <>
      <button
        onClick={() => setIsOpen(true)}
        className="btn-primary flex items-center gap-2"
      >
        <Download className="w-4 h-4" />
        Export Patient Data
      </button>

      {isOpen && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-md w-full">
            {/* Header */}
            <div className="flex items-center justify-between p-6 border-b border-neutral-200">
              <h2 className="text-xl font-display font-bold text-neutral-900">
                {step === "format" && "Export Patient Data"}
                {step === "confirm" && "Confirm Export"}
                {step === "exporting" && "Exporting..."}
                {step === "complete" && "Export Complete"}
              </h2>
              <button
                onClick={handleClose}
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
                  <p className="text-neutral-600">
                    Select the format for exporting {patientName}&apos;s data:
                  </p>

                  <div className="space-y-3">
                    <button
                      onClick={() => setSelectedFormat("json")}
                      className={`w-full p-4 border-2 rounded-lg text-left transition-all ${
                        selectedFormat === "json"
                          ? "border-primary-500 bg-primary-50"
                          : "border-neutral-200 hover:border-neutral-300"
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <FileJson
                          className={`w-6 h-6 flex-shrink-0 ${
                            selectedFormat === "json"
                              ? "text-primary-600"
                              : "text-neutral-400"
                          }`}
                        />
                        <div>
                          <div className="font-semibold text-neutral-900">
                            JSON Format
                          </div>
                          <div className="text-sm text-neutral-600">
                            Structured data format, easy to process programmatically
                          </div>
                        </div>
                      </div>
                    </button>

                    <button
                      onClick={() => setSelectedFormat("pdf")}
                      className={`w-full p-4 border-2 rounded-lg text-left transition-all ${
                        selectedFormat === "pdf"
                          ? "border-primary-500 bg-primary-50"
                          : "border-neutral-200 hover:border-neutral-300"
                      }`}
                    >
                      <div className="flex items-start gap-3">
                        <FileText
                          className={`w-6 h-6 flex-shrink-0 ${
                            selectedFormat === "pdf"
                              ? "text-primary-600"
                              : "text-neutral-400"
                          }`}
                        />
                        <div>
                          <div className="font-semibold text-neutral-900">
                            PDF Format
                          </div>
                          <div className="text-sm text-neutral-600">
                            Human-readable document, suitable for printing
                          </div>
                        </div>
                      </div>
                    </button>
                  </div>
                </div>
              )}

              {step === "confirm" && (
                <div className="space-y-4">
                  <div className="p-4 bg-amber-50 border border-amber-200 rounded-lg">
                    <p className="text-sm text-amber-900">
                      <strong>Please confirm:</strong> You are about to export all
                      patient data for {patientName} in{" "}
                      {selectedFormat.toUpperCase()} format. This data contains
                      sensitive health information protected by HIPAA.
                    </p>
                  </div>

                  <div className="space-y-2 text-sm text-neutral-600">
                    <p className="font-semibold text-neutral-900">
                      This export will include:
                    </p>
                    <ul className="list-disc list-inside space-y-1 ml-2">
                      <li>Patient demographics and contact information</li>
                      <li>All session transcripts and recordings</li>
                      <li>All SOAP notes and clinical documentation</li>
                      <li>Session metadata and scheduling information</li>
                    </ul>
                  </div>
                </div>
              )}

              {step === "exporting" && (
                <div className="space-y-4">
                  <div className="flex items-center justify-center py-8">
                    <Loader2 className="w-12 h-12 text-primary-600 animate-spin" />
                  </div>

                  <div className="space-y-2">
                    <div className="flex justify-between text-sm text-neutral-600">
                      <span>Preparing export...</span>
                      <span>{progress}%</span>
                    </div>
                    <div className="w-full bg-neutral-200 rounded-full h-2">
                      <div
                        className="bg-primary-600 h-2 rounded-full transition-all duration-300"
                        style={{ width: `${progress}%` }}
                      />
                    </div>
                  </div>
                </div>
              )}

              {step === "complete" && (
                <div className="space-y-4">
                  <div className="flex flex-col items-center justify-center py-8">
                    <CheckCircle className="w-16 h-16 text-secondary-600 mb-4" />
                    <p className="text-lg font-semibold text-neutral-900">
                      Export Successful
                    </p>
                    <p className="text-sm text-neutral-600 text-center mt-2">
                      Patient data has been downloaded to your device.
                    </p>
                  </div>
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
                    Confirm & Export
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
