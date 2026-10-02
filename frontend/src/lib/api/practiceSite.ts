// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's website — against `backend/app/sites/routes.py`. Every change
 * answers with the whole status, so the page never has to merge one by hand.
 */

import { del, get, post, postForm } from "./client"

export interface SiteDraft {
  file_count: number
  total_bytes: number
  uploaded_at: string
}

export interface SiteVersion {
  version: number
  file_count: number
  total_bytes: number
  published_at: string
  is_live: boolean
}

export interface PracticeSite {
  /** Whether this deployment publishes websites at all. */
  enabled: boolean
  live_version: number | null
  /**
   * The working website host the live version is served at. Set only when a
   * version is published AND a website host is active — the one thing the
   * page may call "live".
   */
  live_host: string | null
  has_active_host: boolean
  draft: SiteDraft | null
  /** Kept versions, newest first. */
  versions: SiteVersion[]
}

export interface SitePreview {
  /** The draft's address on the API's origin, ending in `/`. */
  path: string
  expires_at: string
}

const WEBSITE = "/api/practice/website"

export function getPracticeSite(): Promise<PracticeSite> {
  return get<PracticeSite>(WEBSITE)
}

/** Make a zip of a static folder the draft, replacing any draft. */
export function uploadPracticeSiteDraft(file: File): Promise<PracticeSite> {
  const form = new FormData()
  form.append("file", file)
  return postForm<PracticeSite>(`${WEBSITE}/draft`, form)
}

export function discardPracticeSiteDraft(): Promise<PracticeSite> {
  return del<PracticeSite>(`${WEBSITE}/draft`)
}

/** A new preview address for the draft; the previous one stops working. */
export function previewPracticeSiteDraft(): Promise<SitePreview> {
  return post<SitePreview>(`${WEBSITE}/draft/preview`, {})
}

export function publishPracticeSite(): Promise<PracticeSite> {
  return post<PracticeSite>(`${WEBSITE}/publish`, {})
}

/** Make a kept version live again. */
export function rollBackPracticeSite(version: number): Promise<PracticeSite> {
  return post<PracticeSite>(`${WEBSITE}/versions/${version}/live`, {})
}
