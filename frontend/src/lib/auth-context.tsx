// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  createContext,
  useContext,
  useCallback,
  type ReactNode,
} from "react"
import { getClientAuthProvider } from "@/lib/auth/provider"
import type { AuthUser } from "@/lib/auth/types"

interface AuthContextValue {
  user: AuthUser | null
  loading: boolean
  getIdToken: () => Promise<string | null>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

/**
 * React binding for the active auth provider. Provider-neutral: it asks
 * the selected {@link ClientAuthProvider} for live state and token access,
 * so the rest of the app never touches a concrete auth SDK. The active
 * provider is fixed for the app's lifetime, so calling its `useAuthState`
 * hook here is stable.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const provider = getClientAuthProvider()
  const { user, loading } = provider.useAuthState()

  const getIdToken = useCallback(() => provider.getIdToken(), [provider])

  return (
    <AuthContext.Provider value={{ user, loading, getIdToken }}>
      {children}
    </AuthContext.Provider>
  )
}

const AUTH_DEFAULT: AuthContextValue = {
  user: null,
  loading: false,
  getIdToken: async () => null,
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  // Return a safe default outside of AuthProvider (tests, server components).
  // loading=false means hooks fire immediately — correct for tests and dev mode.
  return context ?? AUTH_DEFAULT
}
