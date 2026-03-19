// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { type ReactNode } from "react"
import { ErrorBoundary } from "./ErrorBoundary"

/**
 * Client-side ErrorBoundary wrapper for the dashboard layout.
 * Needed because the dashboard layout is a server component and
 * cannot directly use client-side ErrorBoundary.
 */
export function DashboardErrorBoundary({ children }: { children: ReactNode }) {
  return <ErrorBoundary>{children}</ErrorBoundary>
}
