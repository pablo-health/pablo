// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"

import { SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { useEmailSender, useSaveEmailSender } from "@/hooks/useEmailSender"
import { ApiError } from "@/lib/api/client"
import type { EmailSender, EmailSenderFields } from "@/lib/api/emailSender"

/** Mirrors `backend/app/portal/client_sender.py`; the server checks them again. */
const MAX_NAME_LENGTH = 100
const MAX_LOCAL_PART_LENGTH = 64
const MAX_REPLY_TO_LENGTH = 254

interface Draft {
  sender_name: string
  sender_local_part: string
  reply_to: string
}

/**
 * Who the practice's email to its clients is from, and where replies go.
 *
 * Three fields, each blank for its default, and a preview line drawn here so it
 * follows every keystroke. Mail leaves from the practice's own domain once that
 * domain's email is verified, and from the deployment's address until then;
 * the server decides which (`resolve_client_sender`), and the preview shows
 * the server's answer whenever the form matches what is saved.
 *
 * Where the deployment's email channel cannot send as the practice, the card
 * says so and offers no form: a preview of a From line no client will see
 * would be untrue.
 */
export function ClientEmailSenderCard() {
  const { data: sender } = useEmailSender()
  if (!sender) return null
  return (
    <SettingsCard
      title="Client email"
      description="Who your emails to clients are from, and where their replies go."
    >
      {sender.applies ? (
        <ClientEmailSenderForm sender={sender} />
      ) : (
        <p className="text-sm text-muted-foreground" data-testid="client-email-not-applicable">
          This deployment sends client email under its own name and address.
        </p>
      )}
    </SettingsCard>
  )
}

function toDraft(chosen: EmailSenderFields): Draft {
  return {
    sender_name: chosen.sender_name ?? "",
    sender_local_part: chosen.sender_local_part ?? "",
    reply_to: chosen.reply_to ?? "",
  }
}

function saveError(error: unknown): string | null {
  if (!error) return null
  if (error instanceof ApiError && error.status === 422 && error.message) return error.message
  return "These settings could not be saved. Try again."
}

function ClientEmailSenderForm({ sender }: { sender: EmailSender }) {
  const save = useSaveEmailSender()
  const saved = toDraft(sender.chosen)
  const [draft, setDraft] = useState<Draft>(saved)
  const [justSaved, setJustSaved] = useState(false)

  const dirty =
    draft.sender_name.trim() !== saved.sender_name ||
    draft.sender_local_part.trim().toLowerCase() !== saved.sender_local_part ||
    draft.reply_to.trim() !== saved.reply_to
  const readOnly = !sender.can_edit
  const error = saveError(save.error)

  function edit(next: Partial<Draft>) {
    setJustSaved(false)
    save.reset()
    setDraft((current) => ({ ...current, ...next }))
  }

  async function onSave() {
    const result = await save.mutateAsync({
      sender_name: draft.sender_name.trim() || null,
      sender_local_part: draft.sender_local_part.trim() || null,
      reply_to: draft.reply_to.trim() || null,
    })
    setDraft(toDraft(result.chosen))
    setJustSaved(true)
  }

  const fromName = draft.sender_name.trim() || sender.defaults.sender_name
  const localPart = draft.sender_local_part.trim().toLowerCase() || sender.defaults.sender_local_part
  const replyTo = draft.reply_to.trim() || sender.defaults.reply_to
  // Matching what is saved, the server's answer is the truth (it also knows
  // when the practice's domain cannot be used). Edited, the preview follows
  // the same rule the server applies.
  const fromAddress = !dirty
    ? (sender.effective.from_address ?? sender.deployment_from_address)
    : sender.sending_domain
      ? `${localPart}@${sender.sending_domain}`
      : sender.deployment_from_address

  return (
    <div className="space-y-4" data-testid="client-email-sender-card">
      <div className="grid gap-4 max-w-md">
        <div className="form-group">
          <Label htmlFor="client-email-sender-name">Sender name</Label>
          <Input
            id="client-email-sender-name"
            value={draft.sender_name}
            placeholder={sender.defaults.sender_name}
            maxLength={MAX_NAME_LENGTH}
            readOnly={readOnly}
            aria-readonly={readOnly}
            onChange={(event) => edit({ sender_name: event.target.value })}
          />
        </div>
        <div className="form-group">
          <Label htmlFor="client-email-local-part">Address on your domain</Label>
          <div className="flex items-center gap-2">
            <Input
              id="client-email-local-part"
              value={draft.sender_local_part}
              placeholder={sender.defaults.sender_local_part}
              maxLength={MAX_LOCAL_PART_LENGTH}
              readOnly={readOnly}
              aria-readonly={readOnly}
              autoCapitalize="none"
              spellCheck={false}
              onChange={(event) => edit({ sender_local_part: event.target.value })}
            />
            <span className="whitespace-nowrap text-sm text-muted-foreground">
              @{sender.sending_domain ?? "your domain"}
            </span>
          </div>
        </div>
        <div className="form-group">
          <Label htmlFor="client-email-reply-to">Replies go to</Label>
          <Input
            id="client-email-reply-to"
            type="email"
            value={draft.reply_to}
            placeholder={sender.defaults.reply_to ?? ""}
            maxLength={MAX_REPLY_TO_LENGTH}
            readOnly={readOnly}
            aria-readonly={readOnly}
            autoComplete="email"
            onChange={(event) => edit({ reply_to: event.target.value })}
          />
        </div>
      </div>

      <div className="space-y-1" data-testid="client-email-preview">
        <p className="text-sm">
          <span className="text-muted-foreground">Clients see: </span>
          <span className="font-medium">{fromName}</span>
          {fromAddress && <span> &lt;{fromAddress}&gt;</span>}
          {replyTo && <span className="text-muted-foreground"> · replies go to {replyTo}</span>}
        </p>
        {!sender.sending_domain && (
          <p className="text-[12.5px] text-muted-foreground" data-testid="client-email-pending-note">
            Once your domain&apos;s email is verified, this switches to {localPart}@your domain
            automatically.
          </p>
        )}
      </div>

      {readOnly ? (
        <p className="text-[12.5px] text-muted-foreground">Only the practice owner can change this.</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            onClick={() => onSave().catch(() => undefined)}
            disabled={!dirty || save.isPending}
          >
            {save.isPending ? "Saving…" : "Save"}
          </Button>
          {justSaved && !dirty && <span className="text-sm text-neutral-600">Saved</span>}
        </div>
      )}
      {error && (
        <p className="text-sm text-red-700" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
