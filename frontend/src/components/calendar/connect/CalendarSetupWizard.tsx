// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useRouter, useSearchParams } from "next/navigation"
import { SetupNav, SetupWizardShell, type SetupStepperStep } from "@/components/setup"
import { CalendarConnectStep } from "./CalendarConnectStep"
import { CalendarSessionsStep } from "./CalendarSessionsStep"
import { CalendarClientsStep } from "./CalendarClientsStep"
import { CalendarReviewStep } from "./CalendarReviewStep"
import {
  completeGoogleCalendarConnect,
  completeGoogleCalendarImportConsent,
  confirmCalendarImport,
  disconnectGoogleCalendar,
  getCalendarBusyWindows,
  getGoogleCalendarAuthUrl,
  getGoogleCalendarConsentOptions,
  getGoogleCalendarStatus,
  setGoogleCalendarEventTitling,
  importNeedsConsent,
  scanCalendarForImport,
  type ConfirmImportResult,
  type GoogleCalendarSelection,
  type ImportProposal,
} from "@/lib/api/scheduling"

const STEPS: SetupStepperStep[] = [
  { id: "connect", label: "Connect" },
  { id: "sessions", label: "Sessions" },
  { id: "clients", label: "Your clients" },
  { id: "review", label: "Review" },
]

const CONNECT_INDEX = 0
const CLIENTS_INDEX = 2
const REVIEW_INDEX = 3

const DEFAULT_SELECTION: GoogleCalendarSelection = {
  write_target: "app_calendar",
  busy: true,
  // Initials, not the generic wording: a column of identical blocks is the
  // problem the choice exists to solve.
  event_titling: "initials",
}

/** Google requires the redirect URI to match one registered on the OAuth
 * client exactly, so the selection can't ride back on the URL. It waits
 * here instead, for the moment the browser lands back on this page. */
const SELECTION_KEY = "pablo.calendar-connect.selection"

/** Set while an incremental IMPORT-capability round trip is in flight, so
 * the code-exchange effect knows this return from Google is "Look at my
 * week" continuing, not a fresh connect. */
const IMPORT_PENDING_KEY = "pablo.calendar-import.pending"

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
      event_titling:
        parsed.event_titling === "generic" || parsed.event_titling === "full"
          ? parsed.event_titling
          : "initials",
    }
  } catch {
    return DEFAULT_SELECTION
  }
}

function rememberImportPending(): void {
  try {
    window.sessionStorage.setItem(IMPORT_PENDING_KEY, "1")
  } catch {
    // Best effort — see rememberSelection.
  }
}

function recallAndClearImportPending(): boolean {
  try {
    const pending = window.sessionStorage.getItem(IMPORT_PENDING_KEY) === "1"
    window.sessionStorage.removeItem(IMPORT_PENDING_KEY)
    return pending
  } catch {
    return false
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

/** A stable two-week window (one back, one ahead) for the pre-scan week
 * grid — frozen for the component's life so it doesn't refetch on every
 * render, and wide enough for a weekly-recurring block to show up at
 * least once regardless of which day "now" happens to land on. */
function busyWindowRange(): { start: string; end: string } {
  const now = new Date()
  const start = new Date(now)
  start.setDate(start.getDate() - 7)
  const end = new Date(now)
  end.setDate(end.getDate() + 7)
  return { start: start.toISOString(), end: end.toISOString() }
}

export function CalendarSetupWizard() {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()

  const [activeIndex, setActiveIndex] = useState(0)
  const [selection, setSelection] = useState<GoogleCalendarSelection>(DEFAULT_SELECTION)
  const [attested, setAttested] = useState(false)
  const [applying, setApplying] = useState(false)
  const [connecting, setConnecting] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Step 3 — the anonymous week grid and the scan it sorts.
  const [busyRange] = useState(busyWindowRange)
  const [proposal, setProposal] = useState<ImportProposal | null>(null)
  const [scanning, setScanning] = useState(false)
  const [scanError, setScanError] = useState<string | null>(null)

  // Step 4 — which proposed series to keep.
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [expanded, setExpanded] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [confirmResult, setConfirmResult] = useState<ConfirmImportResult | null>(null)

  const { data: status } = useQuery({
    queryKey: ["google-calendar", "status"],
    queryFn: getGoogleCalendarStatus,
  })
  const { data: options } = useQuery({
    queryKey: ["google-calendar", "consent-options"],
    queryFn: getGoogleCalendarConsentOptions,
    staleTime: 60 * 60 * 1000,
  })
  const { data: busyWindows } = useQuery({
    queryKey: ["google-calendar", "busy", busyRange.start, busyRange.end],
    queryFn: () => getCalendarBusyWindows(busyRange.start, busyRange.end),
    enabled: Boolean(status?.connected),
  })

  // Show the connected calendar's own choice rather than the default, so
  // step 2 reflects what was actually granted.
  const grantedWriteTarget = status?.connected ? status.write_target : null
  useEffect(() => {
    if (!grantedWriteTarget) return
    setSelection((current) => ({ ...current, write_target: grantedWriteTarget }))
  }, [grantedWriteTarget])

  // Show what the connection is actually set to, not the default.
  const storedTitling = status?.connected ? status.event_titling : null
  useEffect(() => {
    if (!storedTitling) return
    setSelection((current) => ({ ...current, event_titling: storedTitling }))
    setAttested(storedTitling === "full")
  }, [storedTitling])

  // Once a proposal comes in, seed the review step's checkboxes from what
  // the API preselected — never all-checked, never all-unchecked.
  useEffect(() => {
    if (!proposal) return
    setChecked(
      Object.fromEntries(proposal.series.map((series) => [series.candidate_key, series.preselected]))
    )
  }, [proposal])

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

  const runScan = useCallback(async () => {
    setScanning(true)
    setScanError(null)
    try {
      const result = await scanCalendarForImport(redirectUri)
      if (importNeedsConsent(result)) {
        rememberImportPending()
        window.location.assign(result.auth_url)
        return
      }
      setProposal(result)
    } catch (err) {
      setScanError(message(err, "Could not read your calendar. Try again in a moment."))
    } finally {
      setScanning(false)
    }
  }, [redirectUri])

  const code = searchParams.get("code")
  const state = searchParams.get("state") ?? ""
  // An authorization code is single-use, and a re-rendered effect would
  // spend it a second time — which Google rejects.
  const exchangedCode = useRef<string | null>(null)

  useEffect(() => {
    if (!code || exchangedCode.current === code) return
    exchangedCode.current = code
    let cancelled = false

    if (recallAndClearImportPending()) {
      // "Look at my week" sent the therapist to Google for the IMPORT
      // grant alone. Completing it picks the flow back up: land on the
      // clients step and finish what the button started, without making
      // the therapist press it again.
      setScanning(true)
      completeGoogleCalendarImportConsent(code, state, redirectUri)
        .then(() => {
          if (cancelled) return
          queryClient.invalidateQueries({ queryKey: ["google-calendar"] })
          setActiveIndex(CLIENTS_INDEX)
          return runScan()
        })
        .catch((err: unknown) => {
          if (!cancelled) setScanError(message(err, "Google did not finish granting access."))
        })
        .finally(() => {
          if (cancelled) return
          setScanning(false)
          router.replace("/dashboard/settings/calendar")
        })
      return () => {
        cancelled = true
      }
    }

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
  }, [code, state, redirectUri, queryClient, router, runScan])

  // Changing how events read on an already-connected calendar does not
  // need Google again — it is Pablo's own record of what to write, and
  // narrowing it rewrites what has already been written.
  const applyTitling = useCallback(async () => {
    setError(null)
    setApplying(true)
    try {
      await setGoogleCalendarEventTitling(selection.event_titling, attested)
      queryClient.invalidateQueries({ queryKey: ["google-calendar"] })
    } catch (err) {
      setError(message(err, "Could not save how your events should read."))
    } finally {
      setApplying(false)
    }
  }, [attested, queryClient, selection.event_titling])

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

  const finishWizard = useCallback(() => {
    router.push("/dashboard/settings")
  }, [router])

  const handleToggleSeries = useCallback((candidateKey: string) => {
    setChecked((current) => ({ ...current, [candidateKey]: !current[candidateKey] }))
  }, [])

  const handleConfirm = useCallback(async () => {
    if (!proposal) return
    setConfirming(true)
    setConfirmError(null)
    try {
      const series = proposal.series
        .filter((item) => checked[item.candidate_key])
        .map((item) => ({
          candidate_key: item.candidate_key,
          display_name: item.summary,
          start_at: item.first_future_start ?? new Date().toISOString(),
          duration_minutes: item.duration_minutes,
          cadence: item.cadence,
          occurrences: Math.max(item.occurrences_ahead, 1),
          timezone: proposal.timezone,
        }))
      const result = await confirmCalendarImport(series)
      setConfirmResult(result)
    } catch (err) {
      setConfirmError(message(err, "Could not add those clients. Nothing was changed — try again."))
    } finally {
      setConfirming(false)
    }
  }, [proposal, checked])

  const titlingSettled = selection.event_titling !== "full" || attested
  const isLastStep = activeIndex === STEPS.length - 1
  const onReviewStep = activeIndex === REVIEW_INDEX

  return (
    <SetupWizardShell
      steps={STEPS}
      activeIndex={activeIndex}
      onJump={setActiveIndex}
      reachable={() => true}
      title="Google Calendar"
      lede="Put the sessions you book in Pablo onto your calendar."
      onFinishLater={onReviewStep ? undefined : () => router.push("/dashboard/settings")}
      footer={
        onReviewStep ? null : (
          <SetupNav
            onBack={activeIndex > 0 ? () => setActiveIndex(activeIndex - 1) : undefined}
            onContinue={() =>
              isLastStep ? router.push("/dashboard/settings") : setActiveIndex(activeIndex + 1)
            }
            canContinue={
              activeIndex === CONNECT_INDEX
                ? true
                : activeIndex === CLIENTS_INDEX
                  ? proposal !== null
                  : // Full names are the therapist's disclosure to make, so
                    // this step doesn't move on until they've said the
                    // account is covered.
                    titlingSettled && (!isLastStep || Boolean(status?.connected))
            }
            isLastStep={isLastStep}
          />
        )
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
      ) : activeIndex === 1 ? (
        <CalendarSessionsStep
          status={status}
          options={options}
          selection={selection}
          onSelectionChange={setSelection}
          connecting={connecting || applying}
          error={error}
          onConnect={status?.connected ? applyTitling : startConnect}
          attested={attested}
          onAttestedChange={setAttested}
        />
      ) : activeIndex === CLIENTS_INDEX ? (
        <CalendarClientsStep
          busyWindows={busyWindows}
          proposal={proposal}
          scanning={scanning}
          error={scanError}
          onScan={runScan}
          onSkip={finishWizard}
        />
      ) : (
        <CalendarReviewStep
          proposal={proposal}
          checked={checked}
          onToggle={handleToggleSeries}
          expanded={expanded}
          onToggleExpanded={() => setExpanded((value) => !value)}
          onBack={() => setActiveIndex(CLIENTS_INDEX)}
          onReviewAgain={() => setActiveIndex(CLIENTS_INDEX)}
          onConfirm={handleConfirm}
          confirming={confirming}
          error={confirmError}
          result={confirmResult}
          onFinish={() => router.push("/dashboard/calendar")}
        />
      )}
    </SetupWizardShell>
  )
}
