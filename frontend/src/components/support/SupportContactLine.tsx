// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { cn } from "@/lib/utils"
import { supportMailto, useSupportEmail } from "@/lib/support"

interface SupportContactLineProps {
  /** The question before "Email …". Loading failures and crashes read differently. */
  lead?: string
  className?: string
}

/**
 * One line under an error telling the clinician where to write when retrying
 * hasn't helped. Renders nothing when the deployment has no support address.
 *
 * The address is shown as text, not only as a link: a mailto does nothing on a
 * machine with no mail app set up, and the address can still be copied.
 * Kept to the address on purpose: no response time and no named person, since
 * neither is something this code can promise.
 */
export function SupportContactLine({
  lead = "Still not loading?",
  className,
}: SupportContactLineProps) {
  const email = useSupportEmail()
  if (!email) return null
  return (
    <p className={cn("text-xs text-neutral-500", className)} data-testid="support-contact-line">
      {lead} Email{" "}
      <a href={supportMailto(email)} className="text-primary-700 underline hover:text-primary-800">
        {email}
      </a>
      .
    </p>
  )
}
