// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Extension slot for a message at the top of the sign-in form.
 *
 * Ships empty here, so the form starts with its heading as before. A downstream
 * build may replace *this file only* with a component that shows a short
 * message there, such as a note that sign-in is slow right now. It renders
 * inside the card that holds the form, so it is seen at every width, phones
 * included, above the heading. The component should render nothing when there
 * is nothing to say, so the form keeps its usual spacing. Same pattern as
 * `loginBrandPanel.extensions.tsx`.
 */

import type { ComponentType } from "react"

export const LoginTopMessageExtension: ComponentType | null = null
