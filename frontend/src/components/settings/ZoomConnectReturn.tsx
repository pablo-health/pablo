// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The half of the Zoom connect flow that runs after the browser comes back.
 *
 * Approving at Zoom sends the clinician to the settings page with an
 * authorization code on the query string. Something has to notice it and
 * spend it, or the round trip ends with them looking at a card that still
 * says the account is not set up — which is what happened before this
 * existed: the API route was there and reachable and nothing ever called it.
 *
 * Renders nothing on the happy path. The card next to it reads the
 * connection from the providers query, so finishing the exchange and
 * invalidating that query is the whole of "show them it worked".
 *
 * Its own component, behind its own Suspense boundary, because reading the
 * query string is what suspends — and the card should not.
 */

"use client"

import { useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useRouter, useSearchParams } from "next/navigation"
import { AlertCircle } from "lucide-react"
import { useAuth } from "@/lib/auth-context"
import { completeZoomConnect } from "@/lib/api/telehealth"

const CONNECT_FAILED = "Zoom didn't finish connecting. Try connecting again."

export interface ZoomConnectReturnProps {
  /** The query key the settings card reads its connection state from. */
  providersQueryKey: readonly unknown[]
  /**
   * The redirect URI the authorization URL was built with. It has to be the
   * same string here: the deployment checks it again, and Zoom checks that
   * the two agree.
   */
  redirectUri: string
}

export function ZoomConnectReturn({ providersQueryKey, redirectUri }: ZoomConnectReturnProps) {
  const searchParams = useSearchParams()
  const router = useRouter()
  const queryClient = useQueryClient()
  const { user, loading: authLoading } = useAuth()
  const [error, setError] = useState<string | null>(null)

  const code = searchParams.get("code")
  const state = searchParams.get("state") ?? ""
  // An authorization code is single use, and an effect that re-ran would
  // spend it a second time — which Zoom refuses.
  const spentCode = useRef<string | null>(null)

  useEffect(() => {
    if (!code || spentCode.current === code) return
    // Coming back from Zoom is a full page load, and React runs a child's
    // effects before its parents' — so this fires before the provider that
    // initialises auth. Exchanging now would send a request with no
    // Authorization header and spend the one-use code on a 401. `user` is a
    // dependency, so arriving late re-runs this and the exchange proceeds.
    if (authLoading || !user) return
    spentCode.current = code
    let cancelled = false

    completeZoomConnect(code, state, redirectUri)
      .then(async () => {
        // The card renders from this query, so refetching it IS the
        // connected state appearing.
        //
        // Cancelled first, and that is not belt-and-braces. The providers
        // fetch starts on the same page load as this exchange, so it can
        // still be in flight when the exchange lands — and invalidating a
        // query that is already fetching does not start a second fetch. Its
        // pre-connection answer would settle, satisfy the invalidation, and
        // leave the card saying the account is not set up until something
        // else happened to refetch it.
        //
        // Not guarded on `cancelled`: these act on the shared cache rather
        // than on this component's state, so they are safe after unmount and
        // the refreshed answer is worth having either way.
        await queryClient.cancelQueries({ queryKey: providersQueryKey })
        await queryClient.invalidateQueries({ queryKey: providersQueryKey })
      })
      .catch(() => {
        if (!cancelled) setError(CONNECT_FAILED)
      })
      .finally(() => {
        if (cancelled) return
        // Drop the one-use code so a refresh cannot try to spend it again.
        router.replace(window.location.pathname)
      })

    return () => {
      cancelled = true
    }
  }, [code, state, redirectUri, providersQueryKey, queryClient, router, authLoading, user])

  if (error === null) return null

  return (
    <p
      data-testid="telehealth-zoom-connect-error"
      className="flex items-center gap-1.5 text-xs text-red-600"
    >
      <AlertCircle className="h-4 w-4" aria-hidden="true" />
      {error}
    </p>
  )
}
