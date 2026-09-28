// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the signed-in shell hands the page it is showing.
 *
 * The shell is the route group's layout, so it stays mounted while the
 * patient moves between Home and a section; the pages under it are what
 * change. They read the live session and the capability document from here
 * rather than proving the session again on every navigation.
 *
 * Only ever provided in the shell's active phase: a page under the shell
 * never renders before there is a session to hand it.
 */

"use client"

import { createContext, useContext } from "react"
import type { PortalCapabilities } from "@/lib/portal-shell/api"
import type { PortalSlot } from "./slots"

/**
 * The capability document as the pages need it: not yet here, never coming,
 * or here. "Failed" is not "loading" — a page waiting on a document that will
 * never arrive would wait forever — and it is not "loaded" either, because
 * the welcome comes from it.
 */
export type CapabilitiesState =
  | { status: "loading" }
  | { status: "failed" }
  | { status: "loaded"; data: PortalCapabilities }

export interface PortalView {
  slug: string
  sessionToken: string
  capabilities: CapabilitiesState
  /** The practice's name as the header shows it. */
  displayName: string | null
  /** The slots this practice serves, in registration order. */
  slots: PortalSlot[]
  /** Home's address, on whichever form the patient is using. */
  base: string
}

const PortalViewContext = createContext<PortalView | null>(null)

export const PortalViewProvider = PortalViewContext.Provider

export function usePortalView(): PortalView {
  const view = useContext(PortalViewContext)
  if (view === null) {
    throw new Error("usePortalView must be used inside the signed-in portal shell")
  }
  return view
}
