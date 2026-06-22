// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import type { ComponentType, ReactNode } from "react"

/**
 * Extension slot for the provider tree.
 *
 * Ships empty here. A downstream build (e.g. a deployment overlay) may
 * overwrite *this file only* to wrap the whole app in additional providers;
 * `providers.tsx` applies them as the outermost wrappers (just inside
 * `QueryClientProvider`, which needs no network), so they sit above the
 * Oidc/Config/Auth tree. That lets an overlay add a provider without re-
 * declaring the base tree — the wrapper list is the single, stable seam.
 *
 * Order is outermost-first: the first entry wraps everything below it.
 */
export const outerProviderWrappers: Array<ComponentType<{ children: ReactNode }>> = []
