// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ComponentType } from "react"

/**
 * Settings page merge slot: item id → component.
 *
 * The base build adds nothing. A downstream build replaces THIS FILE ONLY to
 * register the pages behind the items it appended to the registry. An id here
 * that the base build also defines wins, which is the escape hatch for a page
 * that genuinely must differ — prefer a render slot in
 * `settingsSlots.extensions.tsx` before reaching for that.
 */
export const settingsPageExtensions: Record<string, ComponentType> = {}
