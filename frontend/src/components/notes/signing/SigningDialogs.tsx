// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The three dialogs around a note's signature: sign and lock, add an
 * addendum, and unlock to correct an error.
 *
 * The name and credentials start from the clinician's profile and can be
 * changed for this one signature; the preview shows the block exactly as it
 * will read. What is stored is what was entered — see `NoteService.sign_note`.
 */

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { useSignerDefaults } from "@/hooks/useNoteSigning"
import { useUserTimeZone } from "@/hooks/usePreferences"
import { formatSignedAt, signedByLine } from "@/lib/utils/signatureBlock"
import type { Note, NoteSignerFields } from "@/types/notes"
import { UpdateChart, useChartUpdates } from "../chartUpdates/UpdateChart"

const errorText = (err: unknown) =>
  err instanceof Error ? err.message : "That didn't work. Try again."

/** Name + credentials inputs and the live preview of the block they make. */
function SignatureFields({
  idPrefix,
  name,
  credentials,
  onNameChange,
  onCredentialsChange,
}: {
  idPrefix: string
  name: string
  credentials: string
  onNameChange: (value: string) => void
  onCredentialsChange: (value: string) => void
}) {
  const timeZone = useUserTimeZone()
  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <Label htmlFor={`${idPrefix}-name`}>Your name</Label>
        <Input
          id={`${idPrefix}-name`}
          value={name}
          maxLength={200}
          onChange={(e) => onNameChange(e.target.value)}
        />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={`${idPrefix}-credentials`}>Credentials</Label>
        <Input
          id={`${idPrefix}-credentials`}
          value={credentials}
          maxLength={200}
          onChange={(e) => onCredentialsChange(e.target.value)}
        />
      </div>
      <div
        className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-800"
        data-testid="signature-preview"
      >
        <p className="text-xs font-medium uppercase tracking-wide text-neutral-500">Preview</p>
        <p>{signedByLine(name.trim() || "…", credentials)}</p>
        <p className="text-neutral-600">{formatSignedAt(new Date().toISOString(), timeZone)}</p>
      </div>
    </div>
  )
}

/** Signer fields that reset to the profile each time a dialog opens. */
function useSignerState() {
  const defaults = useSignerDefaults()
  const [edited, setEdited] = useState<{ name?: string; credentials?: string }>({})
  return {
    name: edited.name ?? defaults.name,
    credentials: edited.credentials ?? defaults.credentials,
    setName: (name: string) => setEdited((prev) => ({ ...prev, name })),
    setCredentials: (credentials: string) => setEdited((prev) => ({ ...prev, credentials })),
    reset: () => setEdited({}),
  }
}

const toSigner = (name: string, credentials: string): NoteSignerFields => ({
  signer_name: name.trim(),
  signer_credentials: credentials.trim() || null,
})

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

type SignNoteDialogProps = DialogProps & {
  onSign: (signer: NoteSignerFields) => Promise<unknown>
  /** The note being signed, so the chart updates it proposes can be decided first. */
  note?: Note
}

export function SignNoteDialog({ note, ...props }: SignNoteDialogProps) {
  return note ? <SignWithChartUpdates note={note} {...props} /> : <SignDialog {...props} />
}

function SignWithChartUpdates({ note, ...props }: SignNoteDialogProps & { note: Note }) {
  const updates = useChartUpdates(props.open ? note : undefined)
  return (
    <SignDialog {...props} undecided={updates.undecided}>
      <UpdateChart note={note} updates={updates} />
    </SignDialog>
  )
}

function SignDialog({
  open,
  onOpenChange,
  onSign,
  undecided = 0,
  children,
}: DialogProps & {
  onSign: (signer: NoteSignerFields) => Promise<unknown>
  /** Chart updates not yet decided; signing leaves them on the note. */
  undecided?: number
  children?: React.ReactNode
}) {
  const signer = useSignerState()
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleOpenChange(next: boolean) {
    if (!next) {
      signer.reset()
      setError(null)
    }
    onOpenChange(next)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setPending(true)
    setError(null)
    try {
      await onSign(toSigner(signer.name, signer.credentials))
      handleOpenChange(false)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Sign and lock note</DialogTitle>
          <DialogDescription>Signing locks the note.</DialogDescription>
        </DialogHeader>
        {children}
        <form onSubmit={handleSubmit} className="space-y-4">
          <SignatureFields
            idPrefix="sign-note"
            name={signer.name}
            credentials={signer.credentials}
            onNameChange={signer.setName}
            onCredentialsChange={signer.setCredentials}
          />
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending || !signer.name.trim()}>
              {/* What is left stays on the signed note, to be decided later. */}
              {pending
                ? "Signing…"
                : undecided > 0
                  ? "Sign without updating"
                  : "Sign and lock"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function AddAddendumDialog({
  open,
  onOpenChange,
  onAdd,
  initialText = "",
}: DialogProps & {
  onAdd: (text: string, signer: NoteSignerFields) => Promise<unknown>
  /** A draft to start from (a dictation's, say); the clinician edits it before signing. */
  initialText?: string
}) {
  const signer = useSignerState()
  const [text, setText] = useState(initialText)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleOpenChange(next: boolean) {
    if (!next) {
      signer.reset()
      setText(initialText)
      setError(null)
    }
    onOpenChange(next)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setPending(true)
    setError(null)
    try {
      await onAdd(text.trim(), toSigner(signer.name, signer.credentials))
      handleOpenChange(false)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add addendum</DialogTitle>
          <DialogDescription>Adds to the signed note without changing it.</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="addendum-text">Addendum</Label>
            <Textarea
              id="addendum-text"
              value={text}
              maxLength={20000}
              onChange={(e) => setText(e.target.value)}
              className="min-h-[120px]"
            />
          </div>
          <SignatureFields
            idPrefix="addendum"
            name={signer.name}
            credentials={signer.credentials}
            onNameChange={signer.setName}
            onCredentialsChange={signer.setCredentials}
          />
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending || !text.trim() || !signer.name.trim()}>
              {pending ? "Adding…" : "Sign and add"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

export function UnlockNoteDialog({
  open,
  onOpenChange,
  onUnlock,
}: DialogProps & { onUnlock: (reason: string) => Promise<unknown> }) {
  const [reason, setReason] = useState("")
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function handleOpenChange(next: boolean) {
    if (!next) {
      setReason("")
      setError(null)
    }
    onOpenChange(next)
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setPending(true)
    setError(null)
    try {
      await onUnlock(reason.trim())
      handleOpenChange(false)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setPending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Unlock note</DialogTitle>
          <DialogDescription>
            Unlock to correct an error. You are responsible for the changes you make. The unlock,
            your reason and your changes are recorded and can be reviewed, and the signed version
            is kept.
          </DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="unlock-reason">Reason for unlocking</Label>
            <Textarea
              id="unlock-reason"
              value={reason}
              maxLength={2000}
              required
              onChange={(e) => setReason(e.target.value)}
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-red-600">
              {error}
            </p>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" disabled={pending || !reason.trim()}>
              {pending ? "Unlocking…" : "Accept & unlock"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
