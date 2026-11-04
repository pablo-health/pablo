// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The practice's "Book a session" link, in the header's button slot, signed in
 * or not: for someone who is not a client yet, and for a client who wants
 * another time. Shown only when the practice turns it on; the address is the
 * practice's choice (backend/app/portal/booking_button.py) — one of its
 * booking links, or a booking page it uses elsewhere. It takes the place of
 * the website's own call to action, which on a practice's website is usually
 * a way to book too, so the header never offers two.
 */

import { Button } from "@/components/ui/button"

export function BookSessionLink({ href }: { href: string }) {
  return (
    <Button asChild size="sm">
      <a href={href} data-testid="portal-book-session">
        Book a session
      </a>
    </Button>
  )
}
