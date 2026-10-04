// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A practice's website — against `backend/app/sites/routes.py`. Every change
 * answers with the whole status, so the page never has to merge one by hand.
 */

import { del, get, post, postForm, put } from "./client"

/** The portal theme a website's `theme.json` gives; a value it does not give is `null`. */
export interface SiteTheme {
  version: number
  colors: Record<"accent" | "accentText" | "background" | "surface" | "text" | "mutedText", string | null>
  fonts: Record<"heading" | "body", string | null>
  radius: "none" | "sm" | "md" | "lg" | null
  /** The portal header that matches the website, from theme.json's `header` block. */
  header: SiteHeader | null
}

export interface SiteHeaderLink {
  label: string
  href: string
}

/** A portal header, as theme.json's `header` block has it. */
export interface SiteHeader {
  wordmark: string | null
  subtitle: string | null
  links: SiteHeaderLink[]
  cta: SiteHeaderLink | null
}

/** A header suggested from the draft's index.html, and what was found but failed and why. */
export interface SiteHeaderSuggestion {
  header: SiteHeader | null
  skipped: { field: string; reason: string }[]
}

export interface SiteThemeReport {
  /** `null` when nothing in `theme.json` could be used. */
  theme: SiteTheme | null
  /** What was left out: where in the file (`colors.text`, or `theme.json` for all of it), and why. */
  skipped: { field: string; reason: string }[]
}

export interface SiteDraft {
  file_count: number
  total_bytes: number
  uploaded_at: string
  /** What the draft's `theme.json` gives the portal; `null` when it has none. */
  theme: SiteThemeReport | null
  /** A portal header suggested from index.html, when theme.json declares none. */
  suggested_header: SiteHeaderSuggestion | null
}

export interface SiteVersion {
  version: number
  file_count: number
  total_bytes: number
  published_at: string
  is_live: boolean
  /** Whether this version gives the portal a theme. */
  has_theme: boolean
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

/** Write a portal header into the draft's theme.json: the suggestion, accepted or edited. */
export function setPracticeSiteDraftHeader(header: SiteHeader): Promise<PracticeSite> {
  return put<PracticeSite>(`${WEBSITE}/draft/header`, header)
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
