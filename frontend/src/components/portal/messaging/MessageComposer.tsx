// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Write into a thread that already exists.
 *
 * Send is blocked while a send is in flight. There is no idempotency key
 * on the route behind it, so a second click would be a second message
 * rather than a retry of the first.
 *
 * The draft is cleared only once the send has resolved. A rejection
 * leaves the text where the patient can see it and try again, and the
 * rejection itself is swallowed here because the caller owns the error
 * copy — nothing about the message is logged or thrown onward.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ExpectationNotice } from "./ExpectationNotice"

export interface MessageComposerProps {
  onSend: (body: string) => Promise<unknown>
  sending: boolean
  slaText?: string | null
  error?: string | null
}

export function MessageComposer({
  onSend,
  sending,
  slaText,
  error,
}: MessageComposerProps) {
  const [body, setBody] = useState("")
  const canSend = body.trim().length > 0 && !sending

  async function handleSend() {
    if (!canSend) return
    try {
      await onSend(body.trim())
      setBody("")
    } catch {
      // The caller renders the failure. Keeping the draft is the point.
    }
  }

  return (
    <div className="flex flex-col gap-3" data-testid="portal-messaging-composer">
      <ExpectationNotice slaText={slaText} />
      <div>
        <Label htmlFor="portal-message-body">Your message</Label>
        <Textarea
          id="portal-message-body"
          data-testid="portal-messaging-composer-body"
          value={body}
          onChange={(event) => setBody(event.target.value)}
          disabled={sending}
          rows={4}
          className="mt-1"
        />
      </div>
      {error && (
        <p data-testid="portal-messaging-composer-error" className="text-sm text-red-600">
          {error}
        </p>
      )}
      <Button
        data-testid="portal-messaging-composer-send"
        onClick={handleSend}
        disabled={!canSend}
      >
        {sending ? "Sending…" : "Send"}
      </Button>
    </div>
  )
}
