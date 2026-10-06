// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import {
  RecordingConsentDialog,
  useRecordingConsentCheck,
  type RecordingConsent,
} from "@/components/sessions/RecordingConsentDialog"
import { Button } from "@/components/ui/button"
import { createLaunchIntent } from "@/lib/api/devices"
import {
  armNoHandoffFallback,
  clickThroughAnchor,
  legacyLaunchUri,
} from "@/lib/companionLaunch"

interface StartSessionButtonProps {
  appointmentId: string
  patientId: string
}

interface ReadyIntent {
  intentId: string
  launchUrl: string
}

/**
 * Issue a launch intent. A failure (backend flag off, network, not
 * authorized) resolves to `null`: the anchor stays inert, no launch attempt.
 */
function issueIntent(
  appointmentId: string,
  aiConsentPrompted = false,
): Promise<ReadyIntent | null> {
  return createLaunchIntent(appointmentId, { aiConsentPrompted })
    .then(({ intent_id, launch_url }) => ({ intentId: intent_id, launchUrl: launch_url }))
    .catch(() => null)
}

/**
 * "Start Session" — hands the appointment off to the enrolled companion via
 * a domain-verified deep link.
 *
 * Flow (per docs/design/companion-thin-client.md):
 *  1. Prefetch the launch intent on hover/focus so the rendered anchor already
 *     has its `launch_url` href when the user clicks. macOS Safari only routes
 *     a Universal Link when the navigation originates from a *real*,
 *     user-activated anchor click — NOT from a `window.location` assignment,
 *     and NOT from a synthetic click fired after an `await` (the user-gesture
 *     context is gone once we round-trip the network). Letting the actual
 *     anchor click drive the navigation is the contract's "preferred" form.
 *  2. On click, with the intent already in hand, the browser navigates to
 *     `launch_url` via the real anchor and we arm a ~1.5s no-handoff timer.
 *     If the companion took over, the page is backgrounded and we cancel.
 *     Otherwise (Firefox / nothing installed) fall back to the legacy
 *     `pablohealth://session/start?intent=<id>` scheme — the SAME single-use
 *     intent, never a second POST.
 *  3. If the click lands before the prefetch resolved (rare: keyboard activate
 *     with no prior focus event, or a slow round-trip), we fetch on click as a
 *     fallback. Safari may not route the Universal Link in that case, but the
 *     legacy no-handoff fallback still delivers the session.
 *  4. The client's answer about AI-assisted notes is prefetched beside the
 *     intent. A declined client, or one nobody has asked yet, opens
 *     RecordingConsentDialog instead of handing off; the hand-off then runs
 *     from the dialog's own button, through the same synthesized anchor as 3.
 *     A failed read does not block: the server refuses a declined client's
 *     recording itself.
 *  5. "Record anyway" hands off a second intent that carries the answer, so
 *     the companion does not ask the same question again. It is minted as
 *     soon as the read says nobody has asked, for the same reason as 1: the
 *     dialog's click has to navigate inside its own gesture.
 */
export function StartSessionButton({ appointmentId, patientId }: StartSessionButtonProps) {
  // The prefetched intent, if any. `null` until the first hover/focus or click.
  const [intent, setIntent] = useState<ReadyIntent | null>(null)
  const [consent, setConsent] = useState<RecordingConsent | null>(null)
  // What the dialog is asking about; `null` while it is closed.
  const [asking, setAsking] = useState<RecordingConsent | null>(null)
  const checkConsent = useRecordingConsentCheck()
  // True from click until the no-handoff window settles, so a rapid second
  // click can't issue a second intent or orphan the first fallback timer.
  const [busy, setBusy] = useState(false)

  const fetchingRef = useRef<Promise<ReadyIntent | null> | null>(null)
  const cleanupRef = useRef<(() => void) | null>(null)

  // Cancel any in-flight no-handoff timer on unmount.
  useEffect(() => {
    return () => cleanupRef.current?.()
  }, [])

  // Run (and clear) any previously-armed fallback before arming a new one, so
  // an earlier timer is never orphaned outside `cleanupRef`.
  const clearFallback = useCallback(() => {
    cleanupRef.current?.()
    cleanupRef.current = null
  }, [])

  // Lazily issue the launch intent so the anchor has a real href at click
  // time. Idempotent: only the first hover/focus actually POSTs, and a click
  // landing while that POST is in flight waits for it rather than dropping.
  const prefetchIntent = useCallback((): Promise<ReadyIntent | null> => {
    if (intent) return Promise.resolve(intent)
    if (fetchingRef.current) return fetchingRef.current
    fetchingRef.current = issueIntent(appointmentId)
      .then((ready) => {
        if (ready) setIntent(ready)
        return ready
      })
      .finally(() => {
        fetchingRef.current = null
      })
    return fetchingRef.current
  }, [appointmentId, intent])

  // The intent "Record anyway" hands off; see 5 above. Issued at most once.
  const [promptedIntent, setPromptedIntent] = useState<ReadyIntent | null>(null)
  const promptedRef = useRef<Promise<ReadyIntent | null> | null>(null)
  const prefetchPromptedIntent = useCallback((): Promise<ReadyIntent | null> => {
    promptedRef.current ??= issueIntent(appointmentId, true).then((ready) => {
      setPromptedIntent(ready)
      return ready
    })
    return promptedRef.current
  }, [appointmentId])

  // Same shape as the intent: one read, shared by hover and click.
  const consentRef = useRef<Promise<RecordingConsent> | null>(null)
  const prefetchConsent = useCallback((): Promise<RecordingConsent> => {
    if (consent) return Promise.resolve(consent)
    consentRef.current ??= checkConsent(patientId)
      .catch((): RecordingConsent => ({ kind: "clear" }))
      .then((read) => {
        setConsent(read)
        if (read.kind === "not_asked") void prefetchPromptedIntent()
        return read
      })
    return consentRef.current
  }, [checkConsent, consent, patientId, prefetchPromptedIntent])

  const prefetch = () => {
    void prefetchIntent()
    void prefetchConsent()
  }

  // Arm the no-handoff fallback for a given intent and hold `busy` for the
  // full window so the button can't be re-triggered mid-handoff.
  const armFallback = useCallback(
    (intentId: string) => {
      clearFallback()
      setBusy(true)
      cleanupRef.current = armNoHandoffFallback(() => {
        clickThroughAnchor(legacyLaunchUri(intentId))
        cleanupRef.current = null
        setBusy(false)
      })
    },
    [clearFallback],
  )

  const onClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
    if (busy) {
      e.preventDefault()
      return
    }
    if (intent && consent?.kind === "clear") {
      // Real, user-activated anchor click → Safari routes the Universal Link
      // via the default navigation. Don't preventDefault; just arm the timer.
      armFallback(intent.intentId)
      return
    }
    if (intent && consent) {
      e.preventDefault()
      setAsking(consent)
      return
    }
    // No prefetched intent or answer yet — fetch on click as a fallback. This
    // breaks the user-gesture chain for the verified link, but the legacy
    // no-handoff fallback still delivers the session.
    e.preventDefault()
    setBusy(true)
    void Promise.all([prefetchIntent(), prefetchConsent()]).then(([ready, read]) => {
      if (!ready) {
        setBusy(false)
        return
      }
      if (read.kind !== "clear") {
        setBusy(false)
        setAsking(read)
        return
      }
      clickThroughAnchor(ready.launchUrl)
      armFallback(ready.intentId)
    })
  }

  const handOff = (ready: ReadyIntent) => {
    clickThroughAnchor(ready.launchUrl)
    armFallback(ready.intentId)
  }

  // From the dialog: asked once, so later clicks on this button hand off directly.
  const startFromDialog = () => {
    setAsking(null)
    setConsent({ kind: "clear" })
    if (intent) handOff(intent)
  }

  const recordAnyway = () => {
    setAsking(null)
    setConsent({ kind: "clear" })
    if (promptedIntent) {
      setIntent(promptedIntent)
      handOff(promptedIntent)
      return
    }
    // Not issued yet (or it failed): wait for it, and if it never comes, hand
    // off the plain intent — the companion then asks once more.
    setBusy(true)
    void prefetchPromptedIntent().then((ready) => {
      const target = ready ?? intent
      if (!target) {
        setBusy(false)
        return
      }
      setIntent(target)
      handOff(target)
    })
  }

  return (
    <>
      <Button
        asChild
        size="sm"
        aria-disabled={busy || undefined}
        onPointerEnter={prefetch}
        onFocus={prefetch}
      >
        <a
          href={intent?.launchUrl ?? "#"}
          rel="noopener"
          onClick={onClick}
        >
          Start session
        </a>
      </Button>
      <RecordingConsentDialog
        patientId={patientId}
        consent={asking}
        onCancel={() => setAsking(null)}
        onStart={startFromDialog}
        onRecordAnyway={recordAnyway}
      />
    </>
  )
}
