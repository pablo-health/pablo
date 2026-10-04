// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import { useAuthQuery } from "@/hooks/useAuthQuery"
import { CheckCircle2 } from "lucide-react"
import { useRouter, useSearchParams } from "next/navigation"
import { useAuth } from "@/lib/auth-context"
import { usePeopleTerm } from "@/hooks/usePeopleTerm"
import { useBooksSessionsNamedInTitle } from "@/components/settings/NameBookingSetting"
import { sayPeople } from "@/lib/peopleTerm"
import {
  SetupNav,
  SetupStepHead,
  SetupWizardShell,
  type SetupStepperStep,
} from "@/components/setup"
import { CalendarConnectStep } from "./CalendarConnectStep"
import { CalendarHoursStep } from "./CalendarHoursStep"
import { CalendarSessionsStep } from "./CalendarSessionsStep"
import { CalendarClientsStep, alreadyComingIn } from "./CalendarClientsStep"
import { CalendarReviewStep } from "./CalendarReviewStep"
import { newClientName, newClientNameFields, type NewClientName } from "./NewClientNameFields"
import { seenElsewhere } from "./WhichClientsList"
import {
  recallAndClearFollowWanted,
  recallAndClearImportPending,
  rememberImportPending,
} from "./importConsent"
import { listFollowableCalendars, setFollowedCalendar } from "@/lib/api/outsideSessions"
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

const GOOGLE_STEPS: SetupStepperStep[] = [
  { id: "connect", label: "Connect" },
  // Not "Calendar": every step of this wizard is about a calendar.
  { id: "sessions", label: "Session calendar" },
  { id: "clients", label: "Your {people}" },
  { id: "review", label: "Review" },
]

/** Prepended where the wizard is the calendar's first run — see
 * `withHoursStep`. */
const HOURS_STEP: SetupStepperStep = { id: "hours", label: "Hours" }

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

function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"
  } catch {
    return "UTC"
  }
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

/** The full-page home of the wizard, and where it sends the browser back to
 * after Google unless a host says otherwise. */
export const CALENDAR_SETUP_PATH = "/dashboard/settings/calendar"

/** Settings > Calendars, where "Keep importing new sessions" lives. Its
 * "Allow access" goes to Google by way of this page (the only redirect
 * registered for it) and comes back here once following is on. */
export const CALENDAR_SETTINGS_PATH = "/dashboard/settings/calendars"

/** What following "the main calendar" is stored as until a read resolves it. */
const MAIN_CALENDAR = "primary"

interface CalendarSetupWizardProps {
  /** The route this wizard is mounted on. Google sends the browser back
   * here after consent, and the one-time code is scrubbed from here
   * afterwards — so it has to be a route the wizard actually renders on,
   * and one registered on the OAuth client. Defaults to the Settings page. */
  returnPath?: string
  /** "Finish later". Defaults to leaving for Settings. */
  onFinishLater?: () => void
  /** The wizard has run its course — last step continued, the week skipped,
   * or the import confirmed. Defaults to leaving for Settings (or the
   * Calendar, after an import). */
  onDone?: () => void
  /** Puts "Hours" in front of Connect. The calendar's first run passes
   * this every time, not only when hours are missing: the browser comes back
   * from Google on a fresh page load, by when the hours exist, and a step
   * that came and went with them renumbered every step after it mid-flow.
   * Connecting a calendar before any rule exists syncs free/busy into a
   * frame that does not exist yet, so the step comes first. Finishing or
   * skipping it only advances the wizard — it is not an answer to the Google
   * steps' own gate. */
  withHoursStep?: boolean
  /** The practice already has hours: "Hours" shows as done and the
   * wizard opens on Connect. Without it the step asks for them. */
  hoursSaved?: boolean
  /** The hours step was saved or skipped, so the host can stop asking. */
  onHoursAnswered?: () => void
}

export function CalendarSetupWizard({
  returnPath = CALENDAR_SETUP_PATH,
  onFinishLater,
  onDone,
  withHoursStep: withHoursStepProp = false,
  hoursSaved: hoursSavedProp = false,
  onHoursAnswered,
}: CalendarSetupWizardProps = {}) {
  const router = useRouter()
  const searchParams = useSearchParams()
  const queryClient = useQueryClient()
  const { user, loading: authLoading } = useAuth()

  // Fixed for the life of the wizard. The host stops asking once rules
  // exist, and the hours step is what creates them: following the prop
  // would drop the step from the stepper mid-save and shift every index
  // under the therapist — landing them past Connect instead of on it.
  const [withHoursStep] = useState(withHoursStepProp)
  const [hoursSaved, setHoursSaved] = useState(hoursSavedProp)
  // The one source of step numbers: the stepper reads this list, and every
  // card's "Step N" is its position in it.
  const people = usePeopleTerm()
  const booksNamedSessions = useBooksSessionsNamedInTitle()
  const steps = (withHoursStep ? [HOURS_STEP, ...GOOGLE_STEPS] : GOOGLE_STEPS).map((s) => ({
    ...s,
    label: sayPeople(s.label, people),
  }))
  const indexOf = (id: string) => steps.findIndex((step) => step.id === id)
  const connectIndex = indexOf("connect")
  const sessionsIndex = indexOf("sessions")
  const clientsIndex = indexOf("clients")
  const reviewIndex = indexOf("review")

  const [activeIndex, setActiveIndex] = useState(() =>
    withHoursStepProp && hoursSavedProp ? connectIndex : 0
  )
  // Set when Google has just finished a connect, so the step it lands on
  // can say so; gone once the therapist moves off that step.
  const [justConnected, setJustConnected] = useState(false)
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
  // Which existing client each series is; null means a new client.
  const [clientFor, setClientFor] = useState<Record<string, string | null>>({})
  // Series marked as not a client, remembered on confirm.
  const [notClient, setNotClient] = useState<Record<string, boolean>>({})
  // Names typed for series that become new clients; none uses the suggestion.
  const [names, setNames] = useState<Record<string, NewClientName>>({})
  const [expanded, setExpanded] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [confirmError, setConfirmError] = useState<string | null>(null)
  const [confirmResult, setConfirmResult] = useState<ConfirmImportResult | null>(null)
  const [followSaving, setFollowSaving] = useState(false)
  const [followError, setFollowError] = useState<string | null>(null)

  // Each waits for sign-in to settle. On a full page load — which is how
  // the browser arrives back from Google — a bare query fires before the
  // session is restored, goes out without a token and reads as "not
  // connected" until a retry lands, so a step that keys on the status
  // (the follow checkbox) would render from a 401 for its first second.
  const { data: status } = useAuthQuery({
    queryKey: ["google-calendar", "status"],
    queryFn: getGoogleCalendarStatus,
  })
  const { data: options } = useAuthQuery({
    queryKey: ["google-calendar", "consent-options"],
    queryFn: getGoogleCalendarConsentOptions,
    staleTime: 60 * 60 * 1000,
  })
  const { data: busyWindows } = useAuthQuery({
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

  // Likewise whether busy times were granted, so step 2 only offers to ask
  // Google again when the therapist has actually changed it.
  const grantedBusy = status?.connected && typeof status.busy === "boolean" ? status.busy : null
  useEffect(() => {
    if (grantedBusy === null) return
    setSelection((current) => ({ ...current, busy: grantedBusy }))
  }, [grantedBusy])

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
    // A certain match is that client, and a name-only match starts on the
    // chart it named; anything less starts as a new client until the
    // therapist picks one of the possible names.
    setClientFor(
      Object.fromEntries(
        proposal.series.map((series) => [
          series.candidate_key,
          series.match.patient?.patient_id ?? series.match.suggested_patient_id ?? null,
        ])
      )
    )
    setNotClient({})
    setNames({})
  }, [proposal])

  // Which calendars can be followed, once events can be read at all.
  const readGranted = Boolean(status?.connected && status.import_granted)
  const { data: followable } = useAuthQuery({
    queryKey: ["google-calendar", "calendars"],
    queryFn: listFollowableCalendars,
    enabled: readGranted,
  })
  const calendars = followable?.calendars ?? null
  const mainCalendarId = calendars?.find((c) => c.primary)?.id ?? null
  // A calendar picked before following is turned on; following follows it.
  const [pickedCalendar, setPickedCalendar] = useState<string | null>(null)

  const followedId = status?.follow_calendar_id ?? null
  const following = Boolean(followedId)
  const asListed = (id: string | null) => (id === MAIN_CALENDAR ? mainCalendarId ?? id : id)
  const followCalendarId = following
    ? asListed(followable?.follow_calendar_id ?? followedId)
    : (pickedCalendar ?? mainCalendarId)
  // The import reads the main calendar, so following that one already
  // brings in what an import would (see CalendarClientsStep).
  const followingMain =
    following &&
    (followedId === MAIN_CALENDAR ||
      (mainCalendarId !== null && followCalendarId === mainCalendarId))

  const saveFollowed = useCallback(
    async (calendarId: string | null) => {
      setFollowSaving(true)
      setFollowError(null)
      try {
        await setFollowedCalendar(calendarId)
        await queryClient.invalidateQueries({ queryKey: ["google-calendar"] })
      } catch (err) {
        setFollowError(message(err, "Could not save that. Try again in a moment."))
      } finally {
        setFollowSaving(false)
      }
    },
    [queryClient]
  )

  const changeFollowing = useCallback(
    (enabled: boolean) => saveFollowed(enabled ? (followCalendarId ?? MAIN_CALENDAR) : null),
    [saveFollowed, followCalendarId]
  )

  const changeFollowCalendar = useCallback(
    (calendarId: string) => {
      setPickedCalendar(calendarId)
      if (following) void saveFollowed(calendarId)
    },
    [following, saveFollowed]
  )

  const redirectUri = typeof window === "undefined" ? "" : `${window.location.origin}${returnPath}`

  const startConnect = useCallback(async () => {
    setError(null)
    setConnecting(true)
    try {
      rememberSelection(selection)
      // A "Scan calendar" consent abandoned at Google leaves its marker
      // behind; left there, this connect's return would be taken for that
      // import grant and exchanged as one, which Google's answer cannot pass.
      recallAndClearImportPending()
      recallAndClearFollowWanted()
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
      // The therapist's own zone: the week is proposed, and the series
      // created, in the hours they keep — not UTC, which put every session
      // hours off and let daylight saving move them.
      const result = await scanCalendarForImport(redirectUri, browserTimeZone())
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
    // Returning from Google is a full page load, and React runs a child's
    // effects before its parents' — so this effect fires before the one in
    // AuthProvider that initializes the auth SDK. Exchanging now sends a
    // request with no Authorization header, and the 401 that comes back
    // carries no error code to retry on. The code is single-use, so that
    // one attempt spends it. Wait for auth to resolve; `user` is a
    // dependency, so arriving late re-runs this and the exchange proceeds.
    if (authLoading || !user) return
    exchangedCode.current = code
    let cancelled = false

    const importPending = recallAndClearImportPending()
    // Started from the "keep importing new sessions" setting: the grant was
    // asked for to turn following on, so do that once it lands and go back
    // to the setting, rather than into an import nobody asked for.
    const followWanted = recallAndClearFollowWanted()
    if (importPending && followWanted) {
      setConnecting(true)
      // Either failure is reported here, where the code is scrubbed, rather
      // than lost on a page that never saw the round trip — and each says
      // which side failed: Google's grant, or Pablo saving the choice after it.
      const stayHere = (report: () => void) => {
        if (cancelled) return
        setConnecting(false)
        setActiveIndex(clientsIndex)
        report()
        router.replace(returnPath)
      }
      completeGoogleCalendarImportConsent(code, state, redirectUri)
        .then(async () => {
          if (cancelled) return
          try {
            await setFollowedCalendar(MAIN_CALENDAR)
          } catch (err) {
            stayHere(() =>
              setFollowError(message(err, "Could not save that. Try again in a moment."))
            )
            return
          }
          await queryClient.invalidateQueries({ queryKey: ["google-calendar"] })
          if (!cancelled) router.replace(CALENDAR_SETTINGS_PATH)
        })
        .catch((err: unknown) =>
          stayHere(() => setScanError(message(err, "Google did not finish granting access.")))
        )
      return () => {
        cancelled = true
      }
    }

    if (importPending) {
      // "Scan calendar" sent the therapist to Google for the IMPORT
      // grant alone. Completing it picks the flow back up: land on the
      // clients step and finish what the button started, without making
      // the therapist press it again. Landing there comes first, so a grant
      // that fails is reported where it was asked for, not on step 1.
      setActiveIndex(clientsIndex)
      setScanning(true)
      completeGoogleCalendarImportConsent(code, state, redirectUri)
        .then(async () => {
          if (cancelled) return
          queryClient.invalidateQueries({ queryKey: ["google-calendar"] })
          return runScan()
        })
        .catch((err: unknown) => {
          if (!cancelled) setScanError(message(err, "Google did not finish granting access."))
        })
        .finally(() => {
          if (cancelled) return
          setScanning(false)
          router.replace(returnPath)
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
        // Land on the session calendar step and say it worked. Connect's
        // "About Google access" says the choices are made there, and a step
        // the therapist never saw should not be ticked as done.
        setJustConnected(true)
        setActiveIndex(sessionsIndex)
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(message(err, "Google did not finish connecting."))
      })
      .finally(() => {
        if (cancelled) return
        setConnecting(false)
        // Drop the one-time code so a refresh doesn't try to reuse it.
        router.replace(returnPath)
      })
    return () => {
      cancelled = true
    }
  }, [
    code,
    state,
    redirectUri,
    returnPath,
    queryClient,
    router,
    runScan,
    authLoading,
    user,
    clientsIndex,
    sessionsIndex,
  ])

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

  const finishLater = useCallback(() => {
    if (onFinishLater) onFinishLater()
    else router.push("/dashboard/settings")
  }, [onFinishLater, router])

  const finishWizard = useCallback(() => {
    if (onDone) onDone()
    else router.push("/dashboard/settings")
  }, [onDone, router])

  // An import just landed clients on the calendar; the natural place to
  // look next is the calendar itself, not Settings.
  const finishAfterImport = useCallback(() => {
    if (onDone) onDone()
    else router.push("/dashboard/calendar")
  }, [onDone, router])

  const handleToggleSeries = useCallback((candidateKey: string) => {
    setChecked((current) => ({ ...current, [candidateKey]: !current[candidateKey] }))
  }, [])

  const handleChooseClient = useCallback((candidateKey: string, patientId: string | null) => {
    setClientFor((current) => ({ ...current, [candidateKey]: patientId }))
  }, [])

  const handleToggleNotClient = useCallback((candidateKey: string) => {
    setNotClient((current) => ({ ...current, [candidateKey]: !current[candidateKey] }))
    // A series that is not a client is not imported either.
    setChecked((current) => ({ ...current, [candidateKey]: false }))
  }, [])

  const handleChangeName = useCallback((candidateKey: string, name: NewClientName) => {
    setNames((current) => ({ ...current, [candidateKey]: name }))
    // Typing a name is an answer: the series is checked to go with it.
    setChecked((current) => ({ ...current, [candidateKey]: true }))
  }, [])

  const handleConfirm = useCallback(async () => {
    if (!proposal) return
    setConfirming(true)
    setConfirmError(null)
    try {
      const series = proposal.series
        .filter(
          (item) =>
            checked[item.candidate_key] &&
            !notClient[item.candidate_key] &&
            !seenElsewhere(item.match)
        )
        .map((item) => {
          const patientId = clientFor[item.candidate_key] ?? null
          return {
            candidate_key: item.candidate_key,
            display_name: item.summary,
            patient_id: patientId,
            // A new client goes with the name typed, or the one filled in.
            ...(patientId
              ? {}
              : newClientNameFields(newClientName(names[item.candidate_key], item.suggested_name))),
            source_identifier: item.source_identifier,
            start_at: item.first_future_start ?? new Date().toISOString(),
            duration_minutes: item.duration_minutes,
            cadence: item.cadence,
            occurrences: Math.max(item.occurrences_ahead, 1),
            timezone: proposal.timezone,
          }
        })
      const notClients = proposal.series
        .filter((item) => notClient[item.candidate_key])
        .map((item) => item.source_identifier)
      const result = await confirmCalendarImport(series, notClients)
      setConfirmResult(result)
    } catch (err) {
      setConfirmError(
        message(err, `Could not add those ${people.many}. Nothing was changed — try again.`),
      )
    } finally {
      setConfirming(false)
    }
  }, [proposal, checked, clientFor, notClient, names, people.many])

  const titlingSettled = selection.event_titling !== "full" || attested
  // Following the main calendar replaces importing from it, so the clients
  // step ends the wizard and the review has nothing to show (see
  // CalendarClientsStep). An import from main is still offered whenever a
  // different calendar, or none, is followed.
  const importSkipped = followingMain && activeIndex >= clientsIndex
  const isLastStep = activeIndex === steps.length - 1 || importSkipped
  const onReviewStep = activeIndex === reviewIndex && !followingMain
  // The hours step owns its own buttons while it asks, and "Finish later"
  // there would answer the Google steps' gate for a question that was not
  // asked. Once the hours are in, it is an ordinary step with the usual nav.
  const onHoursStep = withHoursStep && activeIndex === 0
  const askingHours = onHoursStep && !hoursSaved
  const showConnected = justConnected && activeIndex === sessionsIndex
  const stepNumber = (index: number) => index + 1

  return (
    <SetupWizardShell
      steps={steps}
      activeIndex={activeIndex}
      onJump={(index) => {
        setJustConnected(false)
        setActiveIndex(index)
      }}
      reachable={() => true}
      title={withHoursStep ? "Set up your calendar" : "Google Calendar"}
      lede={
        withHoursStep
          ? "Your hours first, then Google Calendar if you use it."
          : "Add sessions to Google Calendar and avoid double-booking."
      }
      onFinishLater={onReviewStep || askingHours ? undefined : finishLater}
      footer={
        onReviewStep || askingHours ? null : (
          <SetupNav
            onBack={
              activeIndex > 0
                ? () => {
                    setJustConnected(false)
                    setActiveIndex(activeIndex - 1)
                  }
                : undefined
            }
            onContinue={() => {
              setJustConnected(false)
              if (isLastStep) finishWizard()
              else setActiveIndex(activeIndex + 1)
            }}
            canContinue={
              activeIndex === connectIndex || onHoursStep
                ? true
                : importSkipped
                  ? !followSaving
                  : activeIndex === clientsIndex
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
      {showConnected ? (
        <p role="status" className="mb-4 flex items-center gap-2 text-sm font-medium text-green-700">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
          Google Calendar is connected.
        </p>
      ) : null}
      {askingHours ? (
        <CalendarHoursStep
          step={stepNumber(0)}
          onSaved={() => {
            onHoursAnswered?.()
            setHoursSaved(true)
            setActiveIndex(connectIndex)
          }}
          onSkip={() => {
            onHoursAnswered?.()
            setActiveIndex(connectIndex)
          }}
        />
      ) : onHoursStep ? (
        <SetupStepHead
          eyebrow={`Step ${stepNumber(0)}`}
          title="Your hours are saved"
          lede="You can change them any time in Settings."
        />
      ) : activeIndex === connectIndex ? (
        <CalendarConnectStep
          step={stepNumber(connectIndex)}
          status={status}
          connecting={connecting}
          disconnecting={disconnecting}
          error={error}
          onConnect={startConnect}
          onDisconnect={handleDisconnect}
        />
      ) : activeIndex === sessionsIndex ? (
        <CalendarSessionsStep
          step={stepNumber(sessionsIndex)}
          status={status}
          options={options}
          selection={selection}
          onSelectionChange={setSelection}
          connecting={connecting || applying}
          error={error}
          onConnect={startConnect}
          onSaveTitling={applyTitling}
          attested={attested}
          onAttestedChange={setAttested}
        />
      ) : activeIndex === clientsIndex ? (
        <CalendarClientsStep
          step={stepNumber(clientsIndex)}
          busyWindows={busyWindows}
          proposal={proposal}
          scanning={scanning}
          error={scanError}
          onScan={runScan}
          onSkip={finishWizard}
          following={following}
          canFollow={readGranted}
          calendars={calendars}
          followCalendarId={followCalendarId}
          onFollowCalendarChange={changeFollowCalendar}
          followingMain={followingMain}
          onFollowingChange={changeFollowing}
          followSaving={followSaving}
          followError={followError}
          booksNamedSessions={booksNamedSessions}
        />
      ) : followingMain ? (
        <SetupStepHead
          eyebrow={`Step ${stepNumber(reviewIndex)}`}
          title="Nothing to import"
          lede={alreadyComingIn(calendars?.find((c) => c.id === followCalendarId))}
        />
      ) : (
        <CalendarReviewStep
          step={stepNumber(reviewIndex)}
          proposal={proposal}
          checked={checked}
          onToggle={handleToggleSeries}
          clientFor={clientFor}
          onChooseClient={handleChooseClient}
          notClient={notClient}
          onToggleNotClient={handleToggleNotClient}
          names={names}
          onChangeName={handleChangeName}
          expanded={expanded}
          onToggleExpanded={() => setExpanded((value) => !value)}
          onBack={() => setActiveIndex(clientsIndex)}
          onReviewAgain={() => setActiveIndex(clientsIndex)}
          onConfirm={handleConfirm}
          confirming={confirming}
          error={confirmError}
          result={confirmResult}
          onFinish={finishAfterImport}
        />
      )}
    </SetupWizardShell>
  )
}
