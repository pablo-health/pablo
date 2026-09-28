// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useClientInvitePreview } from "@/hooks/useInviteTemplate"

/**
 * The invitation this client will get, as the server would send it now.
 *
 * Rendered by the same code that sends it, so what is shown here is the
 * email; only the sign-in link is withheld, because it is a credential and
 * does not exist until Send mints it.
 */
export function InviteEmailPreview({
  patientId,
  versionIds,
}: {
  patientId: string
  versionIds: string[]
}) {
  const { data, isLoading, error } = useClientInvitePreview(patientId, versionIds, true)

  if (isLoading) {
    return <p className="text-sm text-neutral-500">Loading the email…</p>
  }
  if (error || !data?.available) {
    return (
      <p className="text-sm text-neutral-500" data-testid="invite-email-preview-unavailable">
        The email could not be loaded.
      </p>
    )
  }

  return (
    <div
      className="space-y-2 rounded-lg border border-border bg-neutral-50 p-3 text-sm"
      data-testid="invite-email-preview"
    >
      <dl className="grid grid-cols-[auto,1fr] gap-x-3 gap-y-1">
        <dt className="text-neutral-500">To</dt>
        <dd data-testid="invite-email-preview-to">{data.to_email}</dd>
        <dt className="text-neutral-500">Subject</dt>
        <dd className="font-medium" data-testid="invite-email-preview-subject">
          {data.subject}
        </dd>
      </dl>
      <pre
        className="whitespace-pre-wrap border-t border-border pt-2 font-sans text-neutral-800"
        data-testid="invite-email-preview-text"
      >
        {data.text}
      </pre>
    </div>
  )
}
