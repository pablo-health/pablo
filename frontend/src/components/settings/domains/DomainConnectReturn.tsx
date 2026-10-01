// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Coming back to Settings > Domains from a DNS provider's one-click setup.
 *
 * The provider returns the practice here with the `state` the link carried
 * and, if it made no change, an `error`. Neither is taken as the outcome: the
 * server checks the state is one it issued for this practice, then runs the
 * ordinary DNS check, and the records table shows what that found. So the
 * words here say a check happened, never that the setup is done.
 *
 * The query is read from `window.location` once, on the full page load the
 * provider's redirect causes, and cleared with `history.replaceState` (which
 * the app router keeps in step with) so a reload does not send it again.
 */

"use client"

import { useEffect, useRef } from "react"
import { useDomainConnectReturn } from "@/hooks/useDomainConnect"
import { ApiError } from "@/lib/api/client"
import { useAuth } from "@/lib/auth-context"

interface Returned {
  state: string
  error?: string
}

function readReturn(): Returned | null {
  const params = new URLSearchParams(window.location.search)
  const state = params.get("state")
  if (!state) return null
  const error = params.get("error")
  return error ? { state, error } : { state }
}

export function DomainConnectReturn() {
  const { user, loading: authLoading } = useAuth()
  const { mutate, isPending, isError, error, data } = useDomainConnectReturn()
  // undefined: the URL has not been read yet; null: nothing (left) to send.
  const returned = useRef<Returned | null | undefined>(undefined)

  useEffect(() => {
    if (returned.current === undefined) {
      returned.current = readReturn()
      if (returned.current) window.history.replaceState(window.history.state, "", window.location.pathname)
    }
    // Arriving back is a full page load; wait for sign-in before asking.
    if (!returned.current || authLoading || !user) return
    const body = returned.current
    returned.current = null
    mutate(body)
  }, [authLoading, user, mutate])

  if (isPending) {
    return (
      <p role="status" className="mb-3 text-[12.5px] text-muted-foreground">
        Checking your records…
      </p>
    )
  }
  if (isError) {
    return (
      <p role="alert" className="mb-3 text-[12.5px] text-red-700" data-testid="domain-connect-error">
        {error instanceof ApiError && error.message ? error.message : "Your records couldn't be checked. Try again."}
      </p>
    )
  }
  if (!data) return null
  return (
    <p role="status" className="mb-3 text-[12.5px] text-muted-foreground" data-testid="domain-connect-result">
      {data.error
        ? "Your DNS provider didn't make the change. Below is what we found in your DNS."
        : "Below is what we found in your DNS. New records can take a few minutes to show up."}
    </p>
  )
}
