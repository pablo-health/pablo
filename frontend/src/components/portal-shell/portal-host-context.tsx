// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Whether the portal is being served on the practice's own host. The portal
 * layout decides it on the server from the host lookup
 * (`@/lib/portal-host/practice-host-request`) and hands it down here, so the
 * pages beneath can look like part of the practice's site there and keep the
 * portal's own look everywhere else.
 *
 * Decided by the host, never by whether the practice has a theme: a practice
 * on its own domain with no theme.json is still on its own domain.
 */

"use client"

import { createContext, useContext } from "react"

export interface PortalHostInfo {
  /** Served on a host the practice holds, rather than this deployment's own. */
  onPracticeHost: boolean
}

const PortalHostContext = createContext<PortalHostInfo>({ onPracticeHost: false })

export function PortalHostProvider({ value, children }: { value: PortalHostInfo; children: React.ReactNode }) {
  return <PortalHostContext.Provider value={value}>{children}</PortalHostContext.Provider>
}

export function usePortalHost(): PortalHostInfo {
  return useContext(PortalHostContext)
}
