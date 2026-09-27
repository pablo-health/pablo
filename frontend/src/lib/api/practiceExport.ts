// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { postFile } from "./client"

export interface PracticeExportOptions {
  includeTranscripts: boolean
  includePsychotherapyNotes: boolean
}

/**
 * Download the practice as one file: every chart's archive, the practice-wide
 * CSV files, the audit log and a manifest. The route records what left.
 *
 * A blob, because the only thing this side does with it is save it. The
 * archive names itself through Content-Disposition.
 */
export async function downloadPracticeExport(
  options: PracticeExportOptions,
  token?: string,
): Promise<{ blob: Blob; filename: string }> {
  const file = await postFile(
    "/api/admin/tenant-export",
    {
      include_transcripts: options.includeTranscripts,
      include_psychotherapy_notes: options.includePsychotherapyNotes,
    },
    token,
  )
  return { blob: file.blob, filename: file.filename ?? "practice-export.zip" }
}
