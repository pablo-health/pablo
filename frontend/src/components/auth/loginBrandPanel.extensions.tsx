// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Extension slot for the panel beside the sign-in form at desktop width.
 *
 * Ships empty here, so the sign-in screen renders its own panel. A downstream
 * build may replace *this file only* to lay that panel out its own way, the
 * same pattern as `AuthFooterConfig.extensions.ts`. Only the wide-screen panel
 * is replaced: the phone layout, the form and everything that happens after a
 * credential resolves stay in `LoginScreen.tsx`, so a fix there reaches every
 * build.
 */

import type { ComponentType } from "react"

export const LoginBrandPanelExtension: ComponentType | null = null
