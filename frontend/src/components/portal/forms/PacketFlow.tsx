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
import { everyItemVisible, ruleOf, type VisibilityRule } from "@/lib/intake/visibility"
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
  /** The visibility seam, so a test can pin the walk against a real rule. */
  visibility?: VisibilityRule
}

export function PacketFlow({
  sessionToken,
  assignmentId,
  form,
  onSessionLost,
  onChanged,
  onClose,
  visibility = everyItemVisible,
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

  /** The questions this patient is shown, in the order the form asks them. */
  const items = useMemo(() => {
    if (detail === null) return []
    const answers = Object.fromEntries(detail.items.map((item) => [item.key, item.value]))
    return [...detail.items]
      .sort((a, b) => a.position - b.position)
      .filter((item) => visibility(ruleOf(item.config), answers))
  }, [detail, visibility])

  /** What is on screen: this sitting's edits over what is already saved. */
  const values = useMemo(
    () =>
      Object.fromEntries(
        items.map((item) => [item.id, edits[item.id] ?? item.value]),
      ) as Record<string, AnswerValue | null>,
    [items, edits],
  )

  const countable = items.filter((item) => rendererFor(item.item_type).answerable)

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
  // question the server called outstanding.
  const current = screen ?? resumeAt(items, assignment.data.progress.missing)

  async function advanceFrom(index: number) {
    const item = items[index]
    const value = values[item.id] ?? null
    setError(null)

    // A heading, a paragraph, or a question this portal cannot ask yet:
    // there is nothing the save route would accept, so the press only moves.
    // An optional question nobody touched is the same — sending `{}` would
    // be answering it with nothing.
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
 * Where to land when a form is opened.
 *
 * The first id the server called outstanding, or the review screen when it
 * called nothing outstanding. An id naming a question this patient is not
 * shown cannot happen today — the seam shows every question — and is treated
 * as "start at the beginning" rather than as an error if it ever does.
 */
function resumeAt(items: IntakeAssignmentItem[], missing: string[]): Screen {
  if (missing.length === 0 || items.length === 0) return { kind: "review" }
  const index = items.findIndex((item) => item.id === missing[0])
  return { kind: "item", index: index < 0 ? 0 : index }
}
