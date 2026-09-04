// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

/**
 * Manage passkeys (WebAuthn): list enrolled authenticators, add a new one,
 * and revoke. Enrolment runs the browser registration ceremony via
 * `@simplewebauthn/browser` against the backend begin/finish endpoints.
 *
 * The first passkey can be added from a normal session; adding another (or
 * removing one) requires an MFA-satisfied session — the backend enforces
 * this and returns `MFA_REQUIRED`, which we surface plainly.
 */

import { useState } from "react"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { startRegistration, WebAuthnError } from "@simplewebauthn/browser"
import { Fingerprint, Loader2, Trash2 } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ApiError } from "@/lib/api/client"
import {
  beginRegistration,
  finishRegistration,
  listPasskeys,
  revokePasskey,
  type PasskeyCredentialSummary,
} from "@/lib/api/passkey"
import { ListRow } from "./ui"

const PASSKEYS_QUERY_KEY = ["passkeys"] as const

function formatDate(value: string | null): string {
  if (!value) return "never"
  return new Date(value).toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  })
}

function enrollErrorMessage(err: unknown): string | null {
  // The user dismissed the platform prompt — not an error worth shouting about.
  if (err instanceof WebAuthnError && err.name === "NotAllowedError") return null
  if (err instanceof ApiError && err.code === "MFA_REQUIRED") {
    return "Verify an existing passkey before adding another."
  }
  if (err instanceof WebAuthnError && err.name === "InvalidStateError") {
    return "This device already has a passkey for your account."
  }
  return "Could not add the passkey. Please try again."
}

export function PasskeySettings() {
  const queryClient = useQueryClient()
  const [label, setLabel] = useState("")
  const [error, setError] = useState<string | null>(null)

  const { data: passkeys, isLoading } = useQuery({
    queryKey: PASSKEYS_QUERY_KEY,
    queryFn: listPasskeys,
    staleTime: 60 * 1000,
  })

  const addMutation = useMutation({
    mutationFn: async (deviceLabel: string) => {
      const options = await beginRegistration()
      const response = await startRegistration({ optionsJSON: options })
      await finishRegistration(response, deviceLabel.trim() || null)
    },
    onSuccess: () => {
      setLabel("")
      setError(null)
      void queryClient.invalidateQueries({ queryKey: PASSKEYS_QUERY_KEY })
    },
    onError: (err) => {
      const message = enrollErrorMessage(err)
      if (message) setError(message)
    },
  })

  const revokeMutation = useMutation({
    mutationFn: (credentialId: string) => revokePasskey(credentialId),
    onSuccess: () => {
      setError(null)
      void queryClient.invalidateQueries({ queryKey: PASSKEYS_QUERY_KEY })
    },
    onError: (err) => {
      setError(
        err instanceof ApiError && err.code === "MFA_REQUIRED"
          ? "Verify a passkey before removing one."
          : "Could not remove the passkey. Please try again.",
      )
    },
  })

  return (
    <div className="space-y-4">
      {isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading passkeys…
        </div>
      ) : passkeys && passkeys.length > 0 ? (
        <ul>
          {passkeys.map((passkey) => (
            <PasskeyRow
              key={passkey.credential_id}
              passkey={passkey}
              onRevoke={() => revokeMutation.mutate(passkey.credential_id)}
              isRevoking={
                revokeMutation.isPending &&
                revokeMutation.variables === passkey.credential_id
              }
            />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">No passkeys yet.</p>
      )}

      <div className="flex flex-col gap-3 border-t border-border pt-4 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label htmlFor="passkey-label" className="mb-1.5 block text-sm font-medium text-foreground">
            Name (optional)
          </label>
          <Input
            id="passkey-label"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder="e.g. MacBook Touch ID"
            maxLength={120}
            disabled={addMutation.isPending}
          />
        </div>
        <Button
          onClick={() => addMutation.mutate(label)}
          disabled={addMutation.isPending}
          className="gap-2"
        >
          {addMutation.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Fingerprint className="h-4 w-4" />
          )}
          {addMutation.isPending ? "Waiting for device…" : "Add a passkey"}
        </Button>
      </div>

      <p className="text-[12.5px] text-muted-foreground">
        Adding or removing a passkey asks you to verify an existing one first.
      </p>

      {error && (
        <p className="text-sm text-destructive" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

function PasskeyRow({
  passkey,
  onRevoke,
  isRevoking,
}: {
  passkey: PasskeyCredentialSummary
  onRevoke: () => void
  isRevoking: boolean
}) {
  return (
    <ListRow
      icon={Fingerprint}
      title={passkey.device_label || "Passkey"}
      subtitle={`Added ${formatDate(passkey.created_at)} · Last used ${formatDate(passkey.last_used_at)}`}
    >
      <Button
        variant="ghost"
        size="icon-sm"
        onClick={onRevoke}
        disabled={isRevoking}
        className="text-destructive hover:bg-destructive/10 hover:text-destructive"
        aria-label={`Remove ${passkey.device_label || "passkey"}`}
      >
        {isRevoking ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <Trash2 className="h-4 w-4" />
        )}
      </Button>
    </ListRow>
  )
}
