// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * One form, walked a question at a time.
 *
 * A stepped walk rather than one long page. Most people meet this on a
 * phone, and sixteen measure items plus a free-text box on a single scroll
 * is the shape that gets abandoned halfway.
 *
 * **Where it resumes is the server's answer.** `progress.missing` holds item
 * ids in the order the form asks them, so "continue" means the first id it
 * names — not the furthest screen this browser remembers, which would send
 * somebody back to a question they answered on another device.
 *
 * **Save on Continue, never on a timer.** One PUT per press, and the press
 * is what advances. No autosave is a deliberate choice rather than a missing
 * feature: fewer writes of a person's clinical answers, and a state the
 * patient can see. Back saves nothing — stepping back to re-read is not
 * answering, and a save on the way out would send a half-typed answer.
 *
 * **Nothing here decides whether the form is finished.** Sending is offered
 * from the review screen whatever the answers look like; the server refuses
 * an unfinished form and names what is outstanding. A question somebody was
 * never shown and a question they skipped look the same from a browser.
 *
 * **A form sent back shows only what was asked about.** When the assignment
 * carries a `correction`, the walk is the questions it names and nothing
 * else — the rest have been read and kept, and putting them back on screen
 * would invite changes the server refuses with a 409. Which questions those
 * are is the server's answer too, carried on the row rather than worked out
 * here.
 */

"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  fetchAssignment,
  PatientIntakeError,
  saveAnswer,
  submitAssignment,
  type IntakeAssignmentItem,
  type IntakeForm,
  type IntakeReceipt,
} from "@/lib/api/patientIntake"
import { evaluate, ruleOf, type VisibilityMap } from "@/lib/intake/visibility"
import { RATE_LIMITED, SAVE_FAILED, SUBMIT_FAILED } from "./formsCopy"
import { FormsAlreadySent, FormsLoadFailed, FormsLoading } from "./FormsNotice"
import { ItemScreen } from "./ItemScreen"
import { ReceiptScreen } from "./ReceiptScreen"
import { rendererFor } from "./renderers/registry"
import type { AnswerValue } from "./renderers/types"
import { ReviewScreen } from "./ReviewScreen"

export const assignmentKey = (token: string, id: string) =>
  ["patient-intake", "assignment", token, id] as const

type Screen =
  | { kind: "item"; index: number }
  | { kind: "review" }
  | { kind: "receipt"; receipt: IntakeReceipt }
  | { kind: "closed" }

export interface PacketFlowProps {
  sessionToken: string
  assignmentId: string
  form: IntakeForm | null
  /** Raised when a request comes back 401 or 403: the shell owns both. */
  onSessionLost: () => void
  /** Called after any write, so the list behind this can refetch. */
  onChanged: () => void
  onClose: () => void
  /** The visibility seam, so a test can pin the walk against an answer. */
  visibility?: VisibilityMap
}

export function PacketFlow({
  sessionToken,
  assignmentId,
  form,
  onSessionLost,
  onChanged,
  onClose,
  visibility = evaluate,
}: PacketFlowProps) {
  const queryClient = useQueryClient()
  // Null until the patient navigates. Where the walk STARTS is derived from
  // the rows rather than copied into state when they arrive: a copy would
  // have to be kept in step with every refetch, and the server's answer is
  // already here.
  const [screen, setScreen] = useState<Screen | null>(null)
  // Only what the patient changed in this sitting. What is on screen is this
  // over the saved answers, so a refetch brings the server's version through
  // without an effect to re-seed anything.
  const [edits, setEdits] = useState<Record<string, AnswerValue>>({})
  const [error, setError] = useState<string | null>(null)
  // Set synchronously, unlike the mutation's own pending flag: two clicks
  // landing in one React batch would both read the old state and both POST.
  const submitting = useRef(false)

  const assignment = useQuery({
    queryKey: assignmentKey(sessionToken, assignmentId),
    queryFn: () => fetchAssignment(sessionToken, assignmentId),
    retry: false,
  })

  const detail = assignment.data ?? null

  /**
   * Every question on the form, in the order the patient reads them.
   *
   * Sorted before anything reads a rule: a rule may only look backwards,
   * so "earlier on the form" has to mean this order rather than the order
   * the array happened to arrive in.
   */
  const ordered = useMemo(() => {
    if (detail === null) return []
    return [...detail.items].sort((a, b) => a.position - b.position)
  }, [detail])

  /**
   * What every question has been answered, keyed the way a rule points.
   *
   * **This sitting's edits over what is saved, not the saved copy alone.**
   * A rule takes effect the moment the answer it depends on is given —
   * somebody picking "yes" should be asked the follow-up on the next press,
   * not after a round trip that this walk does not make. Reading only the
   * server's copy would leave the question hidden until a refetch, which is
   * a branch that never fires inside one sitting.
   */
  const answers = useMemo(
    () => Object.fromEntries(ordered.map((item) => [item.key, edits[item.id] ?? item.value])),
    [ordered, edits],
  )

  /**
   * The questions this patient is shown.
   *
   * What a rule needs beyond the answers is how many items a measure has,
   * and that arrives with the form rather than with the item — an item
   * carries the measure's code, and the wording and the item count come
   * from the server so the form and the scorer cannot drift.
   *
   * A form sent back for corrections narrows it once more, to the questions
   * the practice named. Narrowing rather than disabling the other screens: a
   * question that cannot be changed is not a question being asked. It runs
   * after the rules rather than instead of them, so a reopened question a
   * rule has since hidden stays hidden.
   */
  const items = useMemo(() => {
    const shown = visibility(
      ordered.map((item) => ({
        key: item.key,
        rule: ruleOf(item.config),
        instrumentItems: measureSize(item, form),
      })),
      answers,
    )
    const visible = ordered.filter((item) => shown[item.key])
    const asked = detail?.correction ?? null
    if (asked === null) return visible
    return visible.filter((item) => asked.item_ids.includes(item.id))
  }, [ordered, answers, detail, form, visibility])

  /** What is on screen: this sitting's edits over what is already saved. */
  const values = useMemo(
    () =>
      Object.fromEntries(
        items.map((item) => [item.id, edits[item.id] ?? item.value]),
      ) as Record<string, AnswerValue | null>,
    [items, edits],
  )

  // The questions that collect something, whether the walk saves it or the
  // renderer writes it for itself. A consent document is counted here and
  // shown on the review screen; what it is NOT is saved on Continue.
  const countable = items.filter((item) => {
    const renderer = rendererFor(item.item_type)
    return renderer.answerable || renderer.writesItself === true
  })

  /**
   * A renderer wrote something through a route of its own.
   *
   * Re-read rather than patched in place: what comes back carries the
   * server's answer about progress, and no client is allowed to work that
   * out for itself.
   *
   * **Pinning the screen first is what stops the re-read moving the
   * patient.** Where the walk sits is derived from `progress.missing` until
   * somebody navigates, so a write that settles the last outstanding
   * question would otherwise make the very next render resume at the review
   * screen — signing a one-question form would whisk it away before the
   * signature it just took had been shown. Writing the current index into
   * state says "the patient is here", and Continue is what moves them.
   */
  const pinAndReread = useCallback(
    (index: number) => {
      setScreen({ kind: "item", index })
      onChanged()
      void queryClient.invalidateQueries({ queryKey: assignmentKey(sessionToken, assignmentId) })
    },
    [onChanged, queryClient, sessionToken, assignmentId],
  )

  const save = useMutation({
    mutationFn: ({ item, value }: { item: IntakeAssignmentItem; value: AnswerValue }) =>
      saveAnswer(sessionToken, assignmentId, item.id, value),
    onSuccess: onChanged,
  })

  const submit = useMutation({
    mutationFn: () => submitAssignment(sessionToken, assignmentId),
    onSuccess: (receipt) => {
      setScreen({ kind: "receipt", receipt })
      onChanged()
      void queryClient.invalidateQueries({ queryKey: assignmentKey(sessionToken, assignmentId) })
    },
  })

  const handleFailure = useCallback(
    (raised: unknown, fallback: string) => {
      if (!(raised instanceof PatientIntakeError)) {
        setError(fallback)
        return
      }
      if (raised.kind === "expired") {
        onSessionLost()
        return
      }
      if (raised.kind === "closed") {
        setScreen({ kind: "closed" })
        return
      }
      if (raised.kind === "rate_limited") {
        setError(RATE_LIMITED)
        return
      }
      setError(raised.serverMessage ?? fallback)
    },
    [onSessionLost],
  )

  // A session that died under an open form is the shell's to answer, and
  // saying so is a state change — so it happens after the render, not in it.
  const loadError = assignment.error
  useEffect(() => {
    if (loadError instanceof PatientIntakeError && loadError.kind === "expired") onSessionLost()
  }, [loadError, onSessionLost])

  if (assignment.isError) {
    return <FormsLoadFailed onRetry={() => void assignment.refetch()} />
  }
  if (assignment.isPending) return <FormsLoading />

  // Where the patient navigated to, or — before they have — the first
  // question the server called outstanding. On a form sent back, that is
  // the first correction still to do rather than the first unanswered
  // question: the rest of the form was finished when it went in.
  const correction = assignment.data.correction
  const current =
    screen ?? resumeAt(items, correction?.outstanding ?? assignment.data.progress.missing)

  async function advanceFrom(index: number) {
    const item = items[index]
    const value = values[item.id] ?? null
    setError(null)

    // A heading, a paragraph, a question this portal cannot ask yet, or one
    // whose renderer already wrote through a route of its own: there is
    // nothing the save route would accept, so the press only moves. An
    // optional question nobody touched is the same — sending `{}` would be
    // answering it with nothing.
    const skip = !rendererFor(item.item_type).answerable || (value === null && !item.required)
    if (!skip) {
      try {
        await save.mutateAsync({ item, value: value ?? {} })
      } catch (raised: unknown) {
        handleFailure(raised, SAVE_FAILED)
        return
      }
    }
    setScreen(index + 1 < items.length ? { kind: "item", index: index + 1 } : { kind: "review" })
  }

  function handleSubmit() {
    if (submitting.current) return
    submitting.current = true
    setError(null)
    submit.mutateAsync().catch((raised: unknown) => {
      handleFailure(raised, SUBMIT_FAILED)
      // Only a failure reopens the button. A recorded submission keeps it
      // shut: this screen is replaced by the receipt, and a retry after a
      // 200 would be a second form with no second set of answers behind it.
      submitting.current = false
    })
  }

  if (current.kind === "closed") return <FormsAlreadySent onClose={onClose} />

  if (current.kind === "receipt") {
    return <ReceiptScreen receipt={current.receipt} onClose={onClose} />
  }

  if (current.kind === "review") {
    return (
      <ReviewScreen
        items={items}
        values={values}
        form={form}
        correction={correction}
        onEdit={(itemId) => {
          const index = items.findIndex((item) => item.id === itemId)
          if (index >= 0) setScreen({ kind: "item", index })
        }}
        onBack={
          items.length === 0
            ? null
            : () => setScreen({ kind: "item", index: items.length - 1 })
        }
        onSubmit={handleSubmit}
        submitting={submit.isPending}
        error={error}
      />
    )
  }

  const item = items[current.index]
  const countableIndex = countable.indexOf(item)

  return (
    <ItemScreen
      item={item}
      value={values[item.id] ?? null}
      onChange={(value) => setEdits((prev) => ({ ...prev, [item.id]: value }))}
      form={form}
      assignmentId={assignmentId}
      sessionToken={sessionToken}
      onWrote={() => pinAndReread(current.index)}
      onSessionLost={onSessionLost}
      onBack={
        current.index === 0
          ? null
          : () => {
              setError(null)
              setScreen({ kind: "item", index: current.index - 1 })
            }
      }
      onContinue={() => void advanceFrom(current.index)}
      saving={save.isPending}
      error={error}
      position={
        countableIndex < 0 ? null : { index: countableIndex + 1, total: countable.length }
      }
    />
  )
}

/**
 * How many items the measure this question asks has, or null.
 *
 * Null for every question that is not a measure, and for a measure this
 * deployment did not send the wording for — which is the same answer,
 * because a rule about a score cannot be settled without knowing when the
 * score is finished, and a measure nobody can be shown will never finish.
 */
function measureSize(item: IntakeAssignmentItem, form: IntakeForm | null): number | null {
  if (item.item_type !== "instrument" || form === null) return null
  const code = item.config.code
  if (typeof code !== "string") return null
  const instrument = form.instruments.find((candidate) => candidate.code === code)
  return instrument === undefined ? null : Object.keys(instrument.items).length
}

/**
 * Where to land when a form is opened.
 *
 * The first id the server called outstanding, or the review screen when it
 * called nothing outstanding. The server does not call a hidden question
 * outstanding, so an id naming one is a browser and a server that disagree
 * about the rules — which is treated as "start at the beginning" rather
 * than as an error, because the walk is for display and the server is what
 * refuses an unfinished form.
 */
function resumeAt(items: IntakeAssignmentItem[], missing: string[]): Screen {
  if (missing.length === 0 || items.length === 0) return { kind: "review" }
  const index = items.findIndex((item) => item.id === missing[0])
  return { kind: "item", index: index < 0 ? 0 : index }
}
