// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react"
import {
  onIdTokenChanged,
  type User,
} from "firebase/auth"
import { initFirebase } from "./firebase"
import { useConfig } from "./config"
import { getCachedTenantId, clearCachedTenantId } from "./tenant"

interface AuthContextValue {
  user: User | null
  loading: boolean
  tenantId: string | null
  getIdToken: () => Promise<string | null>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const config = useConfig()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(!config.devMode)
  const [tenantId, setTenantId] = useState<string | null>(getCachedTenantId)

  useEffect(() => {
    if (config.devMode) return

    const auth = initFirebase({
      apiKey: config.firebaseApiKey,
      authDomain: config.firebaseAuthDomain,
      projectId: config.firebaseProjectId,
      appId: config.firebaseAppId,
    })

    // Set tenant ID on auth instance if cached
    const cached = getCachedTenantId()
    if (cached) {
      auth.tenantId = cached
    }

    return onIdTokenChanged(auth, async (firebaseUser) => {
      if (firebaseUser) {
        const idToken = await firebaseUser.getIdToken()
        // Sync token to server cookie
        await fetch("/api/login", {
          method: "POST",
          headers: { Authorization: `Bearer ${idToken}` },
        })
        setUser(firebaseUser)
      } else {
        // Clear server cookie and tenant cache
        await fetch("/api/logout")
        clearCachedTenantId()
        setTenantId(null)
        setUser(null)
      }
      setLoading(false)
    })
  }, [config])

  const getIdToken = useCallback(async () => {
    if (!user) return null
    return user.getIdToken()
  }, [user])

  return (
    <AuthContext.Provider value={{ user, loading, tenantId, getIdToken }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (!context) {
    throw new Error("useAuth must be used within AuthProvider")
  }
  return context
}
