// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Loader2 } from "lucide-react"
import { SettingsCard } from "../ui"
import { ApiError } from "@/lib/api/client"
import { downloadPracticeExport } from "@/lib/api/practiceExport"
import { saveFile } from "@/lib/saveFile"

type Step = "ready" | "exporting" | "complete"

/** What a refusal means to the person pressing the button. */
function refusalMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "HARDWARE_KEY_REQUIRED") {
      return "Sign in with your security key to export the practice."
    }
    if (error.code === "ADMIN_REQUIRED" || error.status === 403) {
      return "Only an administrator can export the practice."
    }
  }
  return "The export didn't download. Try again."
}

/** Practice > Export. Everything in this practice, as one file. */
export function PracticeExportPage() {
  const [step, setStep] = useState<Step>("ready")
  const [refusal, setRefusal] = useState<string | null>(null)
  // Both start off, matching the export's defaults: the right of access does
  // not reach psychotherapy notes, and transcripts are the rawest part of a
  // chart, so including either is a deliberate choice each time.
  const [includeTranscripts, setIncludeTranscripts] = useState(false)
  const [includePsychotherapyNotes, setIncludePsychotherapyNotes] = useState(false)

  const handleExport = async () => {
    setStep("exporting")
    setRefusal(null)
    try {
      const file = await downloadPracticeExport({ includeTranscripts, includePsychotherapyNotes })
      saveFile(file.blob, file.filename)
      setStep("complete")
    } catch (error) {
      setRefusal(refusalMessage(error))
      setStep("ready")
    }
  }

  return (
    <SettingsCard title="Export practice data" description="Everything in this practice, as one file.">
      <div className="space-y-2">
        <label
          htmlFor="practice-export-include-transcripts"
          className="flex items-start gap-2 text-sm text-foreground"
        >
          <input
            type="checkbox"
            id="practice-export-include-transcripts"
            className="mt-0.5 h-4 w-4"
            checked={includeTranscripts}
            disabled={step === "exporting"}
            onChange={(e) => setIncludeTranscripts(e.target.checked)}
          />
          <span>Include session transcripts</span>
        </label>
        <label
          htmlFor="practice-export-include-psychotherapy-notes"
          className="flex items-start gap-2 text-sm text-foreground"
        >
          <input
            type="checkbox"
            id="practice-export-include-psychotherapy-notes"
            className="mt-0.5 h-4 w-4"
            checked={includePsychotherapyNotes}
            disabled={step === "exporting"}
            onChange={(e) => setIncludePsychotherapyNotes(e.target.checked)}
          />
          <span>Include psychotherapy notes</span>
        </label>
      </div>

      <div className="mt-4 flex items-center gap-3">
        <button
          type="button"
          onClick={handleExport}
          disabled={step === "exporting"}
          className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
        >
          {step === "exporting" && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
          {step === "exporting" ? "Preparing your file" : "Export practice data"}
        </button>
        {step === "complete" && (
          <p role="status" className="text-sm text-muted-foreground">
            Your download has started.
          </p>
        )}
        {refusal && (
          <p role="alert" className="text-sm text-destructive">
            {refusal}
          </p>
        )}
      </div>
    </SettingsCard>
  )
}
