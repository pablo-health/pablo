// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The update notice on the single-VM install (deploy/vm). Practice owner or
 * admin only; both routes answer 404 where the deployment cannot update
 * itself, which the notice reads as "show nothing".
 *
 * `GET /api/software-update` — the running and the latest release.
 * `POST /api/software-update` — ask the host to install the latest release.
 *
 * While the update runs the backend restarts, so progress is read from
 * `/update-status.json`, which the VM's Caddy serves straight from the host.
 */

import { get, post } from "./client"

export type UpdateState = "idle" | "updating" | "done" | "failed" | "rolled_back"

export interface UpdateStatus {
  state: UpdateState
  from: string
  to: string
  message: string
  at: string
}

export interface SoftwareUpdate {
  current: string
  latest: string
  notes_url: string
  available: boolean
  status: UpdateStatus | null
}

export async function fetchSoftwareUpdate(): Promise<SoftwareUpdate> {
  return get<SoftwareUpdate>("/api/software-update")
}

export async function requestSoftwareUpdate(): Promise<{ requested: string }> {
  return post<{ requested: string }>("/api/software-update", {})
}

/** The host's progress file, or null while it cannot be read. */
export async function fetchUpdateStatus(): Promise<UpdateStatus | null> {
  try {
    const response = await fetch("/update-status.json", { cache: "no-store" })
    return response.ok ? ((await response.json()) as UpdateStatus) : null  // the host writes this shape; nothing validates it at runtime
  } catch {
    return null
  }
}
