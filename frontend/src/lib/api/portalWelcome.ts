// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's portal welcome — against
 * `backend/app/portal/welcome_routes.py`.
 *
 * `practice_name` is what `{practice_name}` becomes in the portal, so the
 * editor can draw its preview without asking the server on every keystroke.
 */

import { del, get, put } from "./client"

export interface WelcomePlaceholder {
  name: string
  label: string
}

export interface PortalWelcome {
  heading: string
  body: string
  is_default: boolean
  practice_name: string
  placeholders: WelcomePlaceholder[]
}

export interface PortalWelcomeDraft {
  heading: string
  body: string
}

const WELCOME = "/api/portal/welcome"

export function getPortalWelcome(token?: string): Promise<PortalWelcome> {
  return get<PortalWelcome>(WELCOME, token)
}

export function savePortalWelcome(
  draft: PortalWelcomeDraft,
  token?: string,
): Promise<PortalWelcome> {
  return put<PortalWelcome>(WELCOME, draft, token)
}

export function resetPortalWelcome(token?: string): Promise<PortalWelcome> {
  return del<PortalWelcome>(WELCOME, token)
}
