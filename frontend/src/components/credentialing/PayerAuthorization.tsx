// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import {
  usePayerAuthorization,
  usePayerAuthorizationDocument,
  useRevokePayerAuthorization,
  useSignPayerAuthorization,
} from "@/hooks/useCredentialingChecklist"

/**
 * The permission slip: may Pablo sign her name to a payer's form and ring the
 * payer to chase it?
 *
 * Nothing about credentialing can honestly happen without this, so it sits at
 * the top of the screen rather than buried in settings. It is not a gate on
 * using Pablo — she can do everything else unsigned — which is why it reads as
 * one thing to do rather than as a wall.
 *
 * Renders nothing when the deployment bundles no document. A self-hosted
 * practice has no Pablo staff to authorise, and asking her to sign for a
 * service she is not buying would be worse than silence.
 */
export function PayerAuthorization() {
  const { data, isLoading } = usePayerAuthorization()
  const [reading, setReading] = useState(false)

  if (isLoading || !data?.available) {
    return null
  }

  return (
    <section className="space-y-4">
      {data.signed ? (
        <SignedState
          version={data.signed_version}
          signedAt={data.signed_at}
          signedName={data.signed_name}
          onRead={() => setReading(true)}
        />
      ) : (
        <SigningForm
          version={data.current_version as string}
          superseded={data.superseded}
          previousVersion={data.signed_version}
          reading={reading}
          onRead={() => setReading(true)}
        />
      )}
      {reading && <DocumentText version={data.current_version ?? undefined} />}
    </section>
  )
}

interface SigningFormProps {
  version: string
  superseded: boolean
  previousVersion: string | null
  reading: boolean
  onRead: () => void
}

function SigningForm({
  version,
  superseded,
  previousVersion,
  reading,
  onRead,
}: SigningFormProps) {
  const sign = useSignPayerAuthorization()
  const [name, setName] = useState("")

  return (
    <div className="rounded-lg border border-honey-300 bg-honey-50/50 p-5 space-y-4">
      <div>
        <h3 className="font-display text-lg font-semibold text-neutral-900">
          {/* Somebody who already agreed once is not being asked from scratch,
              and saying so is the difference between "we changed the wording"
              and "we lost your paperwork". */}
          {superseded
            ? "We’ve updated this authorisation"
            : "Let Pablo apply on your behalf"}
        </h3>
        <p className="mt-1 text-sm text-neutral-700">
          {superseded ? (
            <>
              You signed version {previousVersion}. The wording has changed, so
              we need your signature on the current one before we put anything
              else in.
            </>
          ) : (
            <>
              Applying to a panel means signing your name to the payer&rsquo;s
              form and ringing them to chase it. Neither is ours to do without
              being asked in writing.
            </>
          )}
        </p>
      </div>

      {!reading && (
        <Button variant="link" className="h-auto p-0 text-sm" onClick={onRead}>
          Read the authorisation
        </Button>
      )}

      <div className="space-y-2">
        <label
          htmlFor="payer-authorization-name"
          className="block text-sm font-medium text-neutral-900"
        >
          Type your name to sign
        </label>
        <input
          id="payer-authorization-name"
          value={name}
          onChange={(event) => setName(event.target.value)}
          className="w-full max-w-sm rounded-md border border-border px-3 py-2 text-sm"
          autoComplete="off"
        />
      </div>

      <Button
        disabled={name.trim().length === 0 || sign.isPending}
        onClick={() =>
          sign.mutate({ version, signed_name: name.trim(), accepted: true })
        }
      >
        {sign.isPending ? "Signing…" : "Sign"}
      </Button>

      {sign.isError && (
        <p className="text-sm text-muted-foreground">
          That didn&rsquo;t go through. Nothing was signed &mdash; try again in
          a moment.
        </p>
      )}
    </div>
  )
}

interface SignedStateProps {
  version: string | null
  signedAt: string | null
  signedName: string | null
  onRead: () => void
}

function SignedState({ version, signedAt, signedName, onRead }: SignedStateProps) {
  const revoke = useRevokePayerAuthorization()
  const [confirming, setConfirming] = useState(false)

  return (
    <div className="rounded-lg border border-border p-5 space-y-3">
      <p className="text-sm text-neutral-700">
        You authorised Pablo to apply to panels on your behalf
        {signedAt ? ` on ${formatDate(signedAt)}` : ""}
        {signedName ? `, signed ${signedName}` : ""}
        {version ? ` (version ${version})` : ""}.
      </p>

      <div className="flex flex-wrap items-center gap-4">
        <Button variant="link" className="h-auto p-0 text-sm" onClick={onRead}>
          Read what you signed
        </Button>
        {/* Two steps, because withdrawing stops us mid-application and she
            should not be able to do it by mis-clicking. Not a modal: a
            confirmation she cannot read the consequences inside is theatre. */}
        {confirming ? (
          <span className="flex flex-wrap items-center gap-3 text-sm">
            <span className="text-neutral-700">
              We&rsquo;ll stop work on every application in progress.
            </span>
            <Button
              variant="outline"
              size="sm"
              disabled={revoke.isPending}
              onClick={() => revoke.mutate()}
            >
              {revoke.isPending ? "Withdrawing…" : "Withdraw"}
            </Button>
            <Button
              variant="link"
              size="sm"
              className="h-auto p-0"
              onClick={() => setConfirming(false)}
            >
              Keep it
            </Button>
          </span>
        ) : (
          <Button
            variant="link"
            className="h-auto p-0 text-sm text-muted-foreground"
            onClick={() => setConfirming(true)}
          >
            Withdraw it
          </Button>
        )}
      </div>
    </div>
  )
}

function DocumentText({ version }: { version?: string }) {
  const { data, isLoading, isError } = usePayerAuthorizationDocument(true, version)

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading…</p>
  }
  if (isError || data === undefined) {
    return (
      <p className="text-sm text-muted-foreground">
        We couldn&rsquo;t load the text just now. Don&rsquo;t sign what you
        can&rsquo;t read &mdash; try again in a moment.
      </p>
    )
  }

  return (
    <div className="max-h-96 overflow-y-auto rounded-lg border border-border bg-white p-5">
      <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-neutral-800">
        {data}
      </pre>
    </div>
  )
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    day: "numeric",
    month: "long",
    year: "numeric",
  })
}
