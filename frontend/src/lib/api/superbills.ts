// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Superbill API client.
 *
 * Wraps `app.routes.superbills`: one GET that answers with a PDF, or with a
 * 422 listing what the document is missing. The refusal is the interesting
 * answer — it is what the person acts on — so it comes back as a typed error
 * rather than being flattened into the generic `ApiError`.
 */

import { ApiError, buildApiUrl, getAuthHeader } from "./client"

export interface SuperbillFinding {
  severity: "blocking" | "warning"
  code: string
  message: string
  field: string | null
}

/** The route refused to issue the document; `findings` says why. */
export class SuperbillRefusedError extends Error {
  constructor(
    message: string,
    public findings: SuperbillFinding[],
  ) {
    super(message)
    this.name = "SuperbillRefusedError"
  }
}

interface RefusalBody {
  detail?: { message?: string; findings?: SuperbillFinding[] } | string
}

/** The superbill for a client over an inclusive date range, as a PDF blob. */
export async function fetchSuperbill(
  patientId: string,
  start: string,
  end: string,
  token?: string,
): Promise<Blob> {
  const query = new URLSearchParams({ start, end })
  const response = await fetch(buildApiUrl(`/api/patients/${patientId}/superbill?${query}`), {
    method: "GET",
    headers: await getAuthHeader(token),
  })
  if (response.ok) return response.blob()

  if (response.status === 422) {
    const body = (await response.json().catch(() => ({}))) as RefusalBody
    const detail = body.detail
    if (typeof detail === "string") {
      throw new SuperbillRefusedError(detail, [])
    }
    if (detail?.findings) {
      throw new SuperbillRefusedError(
        detail.message ?? "The superbill is missing required information.",
        detail.findings,
      )
    }
  }
  throw new ApiError(
    "UNKNOWN_ERROR",
    `API request failed with status ${response.status}`,
    undefined,
    response.status,
  )
}

/** The filename the route sends, so the download is named the same way. */
export function superbillFilename(start: string, end: string): string {
  return `superbill-${start}-to-${end}.pdf`
}
