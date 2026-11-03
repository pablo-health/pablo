// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's "Book a session" link, beneath whatever the portal is showing,
 * signed in or not: for someone who is not a client yet, and for a client who
 * wants another time. Shown only when the practice turns it on; the address is
 * the practice's choice (backend/app/portal/booking_button.py) — one of its
 * booking links, or a booking page it uses elsewhere.
 */

import { buttonVariants } from "@/components/ui/button"
import { cn } from "@/lib/utils"

export function BookSessionLink({ href }: { href: string }) {
  return (
    <a href={href} data-testid="portal-book-session" className={cn(buttonVariants({ variant: "outline" }), "mt-4 w-full")}>
      Book a session
    </a>
  )
}
