// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Start a new conversation: an optional subject and a first message.
 *
 * The subject is optional because a patient with something to say should
 * not have to name it first. It is offered because a practice reading a
 * list of threads can act faster when one is named.
 *
 * Carries the same expectation notice as the composer. This is often the
 * first message a patient ever writes, so it is the one place the notice
 * must not be missing.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ExpectationNotice } from "./ExpectationNotice"

export interface NewThreadFlowProps {
  onStart: (input: { subject: string | null; body: string }) => Promise<unknown>
  starting: boolean
  slaText?: string | null
  error?: string | null
  onCancel?: () => void
}

export function NewThreadFlow({
  onStart,
  starting,
  slaText,
  error,
  onCancel,
}: NewThreadFlowProps) {
  const [subject, setSubject] = useState("")
  const [body, setBody] = useState("")
  const canSend = body.trim().length > 0 && !starting

  async function handleStart() {
    if (!canSend) return
    try {
      await onStart({
        subject: subject.trim() ? subject.trim() : null,
        body: body.trim(),
      })
      setSubject("")
      setBody("")
    } catch {
      // The caller renders the failure; the draft stays put.
    }
  }

  return (
    <div className="flex flex-col gap-3" data-testid="portal-messaging-new-thread">
      <ExpectationNotice slaText={slaText} />
      <div>
        <Label htmlFor="portal-new-thread-subject">Subject (optional)</Label>
        <Input
          id="portal-new-thread-subject"
          data-testid="portal-messaging-new-thread-subject"
          value={subject}
          onChange={(event) => setSubject(event.target.value)}
          disabled={starting}
          className="mt-1"
        />
      </div>
      <div>
        <Label htmlFor="portal-new-thread-body">Your message</Label>
        <Textarea
          id="portal-new-thread-body"
          data-testid="portal-messaging-new-thread-body"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          disabled={starting}
          rows={4}
          className="mt-1"
        />
      </div>
      {error && (
        <p data-testid="portal-messaging-new-thread-error" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <div className="flex gap-2">
        <Button
          data-testid="portal-messaging-new-thread-send"
          onClick={handleStart}
          disabled={!canSend}
        >
          {starting ? "Sending…" : "Send"}
        </Button>
        {onCancel && (
          <Button
            variant="ghost"
            data-testid="portal-messaging-new-thread-cancel"
            onClick={onCancel}
            disabled={starting}
          >
            Cancel
          </Button>
        )}
      </div>
    </div>
  )
}
