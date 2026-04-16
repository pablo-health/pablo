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

interface AuthContextValue {
  user: User | null
  loading: boolean
  getIdToken: () => Promise<string | null>
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const config = useConfig()
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(!config.devMode)

  useEffect(() => {
    if (config.devMode) return

    const auth = initFirebase({
      apiKey: config.firebaseApiKey,
      authDomain: config.firebaseAuthDomain,
      projectId: config.firebaseProjectId,
      appId: config.firebaseAppId,
    })

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
        await fetch("/api/logout")
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
