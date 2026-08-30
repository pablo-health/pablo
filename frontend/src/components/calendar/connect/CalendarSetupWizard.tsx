// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter, useSearchParams } from "next/navigation"
import { SetupNav, SetupWizardShell, type SetupStepperStep } from "@/components/setup"
import { CalendarConnectStep } from "./CalendarConnectStep"
import { CalendarSessionsStep } from "./CalendarSessionsStep"
import {
  completeGoogleCalendarConnect,
  disconnectGoogleCalendar,
  getGoogleCalendarAuthUrl,
  getGoogleCalendarConsentOptions,
  getGoogleCalendarStatus,
  type GoogleCalendarSelection,
} from "@/lib/api/scheduling"

const STEPS: SetupStepperStep[] = [
  { id: "connect", label: "Connect" },
  { id: "sessions", label: "Sessions" },
]

const DEFAULT_SELECTION: GoogleCalendarSelection = { write_target: "app_calendar", busy: true }

/** Google requires the redirect URI to match one registered on the OAuth
 * client exactly, so the selection can't ride back on the URL. It waits
 * here instead, for the moment the browser lands back on this page. */
const SELECTION_KEY = "pablo.calendar-connect.selection"

function rememberSelection(selection: GoogleCalendarSelection): void {
  try {
    window.sessionStorage.setItem(SELECTION_KEY, JSON.stringify(selection))
  } catch {
    // A browser that refuses session storage still connects; the exchange
    // just falls back to the defaults below.
  }
}

function recallSelection(): GoogleCalendarSelection {
  try {
    const raw = window.sessionStorage.getItem(SELECTION_KEY)
    if (!raw) return DEFAULT_SELECTION
    const parsed = JSON.parse(raw) as Partial<GoogleCalendarSelection>
    return {
      write_target: parsed.write_target === "primary" ? "primary" : "app_calendar",
      busy: parsed.busy !== false,
    }
  } catch {
    return DEFAULT_SELECTION
  }
}

function describeSelection(selection: GoogleCalendarSelection): string {
  const target =
    selection.write_target === "primary"
      ? "writing your sessions to your main calendar"
      : "writing your sessions to a calendar Pablo makes"
  return selection.busy ? `${target}, plus your busy times` : target
}

function message(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback
}

export function CalendarSetupWizard() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()

  const [activeIndex, setActiveIndex] = useState(0)
  const [selection, setSelection] = useState<GoogleCalendarSelection>(DEFAULT_SELECTION)
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const { data: status } = useQuery({
    queryKey: ["google-calendar", "status"],
    queryFn: getGoogleCalendarStatus,
  })
  const { data: options } = useQuery({
    queryKey: ["google-calendar", "consent-options"],
    queryFn: getGoogleCalendarConsentOptions,
    staleTime: 60 * 60 * 1000,
  })

  // Show the connected calendar's own choice rather than the default, so
  // step 2 reflects what was actually granted.
  const grantedWriteTarget = status?.connected ? status.write_target : null
  useEffect(() => {
    if (!grantedWriteTarget) return
    setSelection((current) => ({ ...current, write_target: grantedWriteTarget }))
  }, [grantedWriteTarget])

  const redirectUri =
    typeof window === "undefined" ? "" : `${window.location.origin}/dashboard/settings/calendar`

  const startConnect = useCallback(async () => {
    setError(null)
    setConnecting(true)
    try {
      rememberSelection(selection)
      const { auth_url } = await getGoogleCalendarAuthUrl(redirectUri, selection)
      window.location.assign(auth_url)
    } catch (err) {
      setError(message(err, "Could not reach Google. Try again in a moment."))
      setConnecting(false)
    }
  }, [redirectUri, selection])

  const code = searchParams.get("code")
  const state = searchParams.get("state") ?? ""
  // An authorization code is single-use, and a re-rendered effect would
  // spend it a second time — which Google rejects.
  const exchangedCode = useRef<string | null>(null)

  useEffect(() => {
    if (!code || exchangedCode.current === code) return
    exchangedCode.current = code
    let cancelled = false
    const granted = recallSelection()
    setSelection(granted)
    setConnecting(true)
    completeGoogleCalendarConnect(code, state, redirectUri, granted)
      .then(() => {
        if (cancelled) return
        queryClient.invalidateQueries({ queryKey: ["google-calendar"] })
        setActiveIndex(1)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(message(err, "Google did not finish connecting."))
      })
      .finally(() => {
        if (cancelled) return
        setConnecting(false)
        // Drop the one-time code so a refresh doesn't try to reuse it.
        router.replace("/dashboard/settings/calendar")
      })
    return () => {
      cancelled = true
    }
  }, [code, state, redirectUri, queryClient, router])

  const handleDisconnect = useCallback(async () => {
    setError(null)
    setDisconnecting(true)
    try {
      await disconnectGoogleCalendar()
      queryClient.invalidateQueries({ queryKey: ["google-calendar"] })
    } catch (err) {
      setError(message(err, "Could not disconnect."))
    } finally {
      setDisconnecting(false)
    }
  }, [queryClient])

  const isLastStep = activeIndex === STEPS.length - 1

  return (
    <SetupWizardShell
      steps={STEPS}
      activeIndex={activeIndex}
      onJump={setActiveIndex}
      reachable={() => true}
      title="Google Calendar"
      lede="Put the sessions you book in Pablo onto your calendar."
      onFinishLater={() => router.push("/dashboard/settings")}
      footer={
        <SetupNav
          onBack={activeIndex > 0 ? () => setActiveIndex(activeIndex - 1) : undefined}
          onContinue={() =>
            isLastStep ? router.push("/dashboard/settings") : setActiveIndex(activeIndex + 1)
          }
          canContinue={!isLastStep || Boolean(status?.connected)}
          isLastStep={isLastStep}
        />
      }
    >
      {activeIndex === 0 ? (
        <CalendarConnectStep
          status={status}
          selectionSummary={describeSelection(selection)}
          connecting={connecting}
          disconnecting={disconnecting}
          error={error}
          onConnect={startConnect}
          onDisconnect={handleDisconnect}
        />
      ) : (
        <CalendarSessionsStep
          status={status}
          options={options}
          selection={selection}
          onSelectionChange={setSelection}
          connecting={connecting}
          error={error}
          onConnect={startConnect}
        />
      )}
    </SetupWizardShell>
  )
}
