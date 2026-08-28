// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Firebase implementation of the `/native-auth` surface — the companion
 * desktop app's sign-in handoff. Credential acquisition lives in the shared
 * `CredentialBlock`; this host owns what comes after a credential resolves —
 * exchanging the id token for a one-time authorization code (RFC 8252)
 * handed back to the native app via its custom-scheme redirect.
 */

import { useState, useCallback, useEffect, useRef } from "react"
import { useSearchParams } from "next/navigation"
import { useConfig } from "@/lib/config"
import {
  AuthCard,
  AuthFooter,
  AuthHeader,
  CredentialBlock,
} from "@/components/auth"
import { completionPathAfterHandoff } from "./nativeAuthCompletion"

const ALLOWED_SCHEMES = ["pablohealth", "therapyrecorder"]

export function FirebaseNativeAuthScreen() {
  const searchParams = useSearchParams()
  const config = useConfig()
  const [email, setEmail] = useState("")
  const [notice, setNotice] = useState("")
  const [redirecting, setRedirecting] = useState(false)
  const completionTimer = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (completionTimer.current !== null) window.clearTimeout(completionTimer.current)
    }
  }, [])

  // Validate redirect_uri
  const redirectUri = searchParams.get("redirect_uri")
  // OAuth state (RFC 6749 §10.12) — must be echoed back to redirect_uri unmodified.
  const state = searchParams.get("state")
  const isValidRedirectUri = (() => {
    if (!redirectUri) return false
    try {
      const url = new URL(redirectUri)
      const scheme = url.protocol.replace(":", "")
      // Allow custom URL schemes (macOS)
      if (ALLOWED_SCHEMES.includes(scheme)) return true
      // Allow loopback for native apps (RFC 8252 Section 7.3)
      if (scheme === "http" && (url.hostname === "localhost" || url.hostname === "127.0.0.1")) return true
      return false
    } catch {
      return false
    }
  })()

  const redirectToApp = useCallback(
    async (user: { getIdToken: () => Promise<string>; refreshToken: string }) => {
      setRedirecting(true)
      try {
        const idToken = await user.getIdToken()

        // Exchange tokens for a one-time authorization code (RFC 8252)
        // so raw tokens never appear in URLs
        const res = await fetch(`${config.apiUrl}/api/auth/native/code`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id_token: idToken,
            refresh_token: user.refreshToken,
            redirect_uri: redirectUri,
          }),
        })

        if (!res.ok) {
          const data = await res.json().catch((err) => {
            console.error("authorize response was not JSON:", err)
            return null
          })
          const errorCode = data?.detail?.error?.code ?? data?.error?.code
          if (res.status === 403 && errorCode === "MFA_REQUIRED") {
            const returnParams = new URLSearchParams({ redirect_uri: redirectUri! })
            if (state) returnParams.set("state", state)
            const returnUrl = `/native-auth?${returnParams.toString()}`
            window.location.href = `/mfa-enrollment?returnTo=${encodeURIComponent(returnUrl)}`
            return
          }
          throw new Error("Failed to generate authorization code")
        }

        const { code } = await res.json()
        const callbackUrl = new URL(redirectUri!)
        callbackUrl.searchParams.set("code", code)
        if (state) callbackUrl.searchParams.set("state", state)
        window.location.href = callbackUrl.toString()

        const completionPath = completionPathAfterHandoff(redirectUri!)
        if (completionPath !== null) {
          completionTimer.current = window.setTimeout(() => {
            window.location.replace(completionPath)
          }, 1500)
        }
      } catch {
        setNotice("Failed to get authentication tokens.")
        setRedirecting(false)
      }
    },
    [redirectUri, state, config.apiUrl]
  )

  // If redirect_uri is invalid, show error immediately
  if (!isValidRedirectUri) {
    return (
      <AuthCard>
        <AuthHeader
          title="Invalid Request"
          titleColor="red"
          subtitle={
            !redirectUri
              ? "Missing redirect_uri parameter. This page must be opened from the Pablo app."
              : "Invalid redirect_uri scheme. Only approved native apps may use this page."
          }
        />
      </AuthCard>
    )
  }

  // Redirecting screen — browser stays on this page after opening the app
  if (redirecting) {
    return (
      <AuthCard>
        <AuthHeader
          title="Sign-in complete"
          subtitle="You can close this tab and return to Pablo."
        />
      </AuthCard>
    )
  }

  return (
    <CredentialBlock
      onCredential={(credential) => redirectToApp(credential.user)}
      email={email}
      onEmailChange={setEmail}
      allowSignUp
      notice={notice}
      renderShell={(form) => (
        <AuthCard>
          <AuthHeader
            title="Sign in to Pablo"
            titleSize="4xl"
            subtitle="Sign in to connect your desktop app"
          />

          {form}

          <AuthFooter />
        </AuthCard>
      )}
    />
  )
}
