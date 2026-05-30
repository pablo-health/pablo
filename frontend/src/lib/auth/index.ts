// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Pluggable frontend auth provider — public surface.
 *
 * The app depends on this barrel, not on any concrete auth SDK. A single
 * implementation is selected per deployment by `NEXT_PUBLIC_AUTH_PROVIDER`
 * (default `firebase`). See `docs/internal/identity-provider-migration-design.md`
 * (pablo-saas) for the why.
 */

export * from "./types"
export { getClientAuthProvider, activeAuthProviderId } from "./provider"
export { getAuthSurfaces } from "./surfaces"
export { getServerSession } from "./server"
export { useAuth, AuthProvider } from "@/lib/auth-context"
