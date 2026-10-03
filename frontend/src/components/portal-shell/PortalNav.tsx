// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The shell's header: the practice's name, on the practice's own host a link
 * back to its website, Sign out, and the navigation.
 *
 * Where the website's theme.json declares a header (`./WebsiteHeader`), the
 * practice's own host shows it instead of the plain link back: the wordmark
 * and subtitle linking to the website, its call to action, and its links in
 * a row of their own above the portal's sections. Signed in or out, the
 * header is the same.
 *
 * The navigation is real links — Home, then one per section this practice
 * serves — and the page being shown is marked `aria-current="page"`, so a
 * screen reader hears where it is as well as where it could go. It wraps on
 * a narrow screen rather than folding behind a menu: four or five short
 * words fit, and a menu would hide the only way around.
 */

"use client"

import Link from "next/link"
import { Button } from "@/components/ui/button"
import { portalSectionHref } from "@/lib/portal-shell/paths"
import { usePortalHost } from "./portal-host-context"
import type { PortalSlot } from "./slots"
import { PracticeBrand, WebsiteCta, WebsiteLinks } from "./WebsiteHeader"

const LINK_CLASS =
  "text-sm text-neutral-600 underline-offset-4 hover:underline focus-visible:underline aria-[current=page]:font-semibold aria-[current=page]:text-neutral-900"

export function ShellHeader({
  displayName,
  slots,
  base,
  section,
  onSignOut,
  signingOut,
}: {
  displayName: string | null
  slots: PortalSlot[]
  base: string
  /** The section on screen, or `null` on Home. */
  section: string | null
  onSignOut?: () => void
  signingOut: boolean
}) {
  // Only slots that asked for a label appear in the navigation; a slot
  // without one still has a tile and a page of its own.
  const navSlots = slots.filter((slot) => slot.label !== undefined)
  // On the practice's own host, the way back to the practice's website.
  const { siteHost, header } = usePortalHost()
  return (
    <header
      // With a wordmark of its own on screen, the record's name is still what
      // the header is called.
      aria-label={header && displayName ? displayName : undefined}
      className="border-b border-neutral-200 bg-white px-4 py-4"
    >
      <div className="mx-auto flex max-w-md flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {header ? (
          <PracticeBrand displayName={displayName} header={header} siteHost={siteHost} />
        ) : displayName ? (
          <h1 data-testid="portal-shell-practice-name" className="font-display text-lg font-semibold">
            {displayName}
          </h1>
        ) : (
          <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
        )}
        {header && <WebsiteCta header={header} />}
        {siteHost && !header && (
          // The visible text stays short and the hostname stays out of it; a
          // screen reader also hears whose website. The accessible name starts
          // with the visible words so voice control can still say them.
          <a
            href={`https://${siteHost}`}
            data-testid="portal-back-to-site"
            aria-label={displayName ? `Back to website of ${displayName}` : undefined}
            className={LINK_CLASS}
          >
            Back to website
          </a>
        )}
        {onSignOut && (
          <Button
            data-testid="portal-shell-sign-out"
            onClick={onSignOut}
            disabled={signingOut}
            variant="ghost"
            size="sm"
          >
            {signingOut ? "Signing out…" : "Sign out"}
          </Button>
        )}
        {header && <WebsiteLinks header={header} />}
        {navSlots.length > 0 && (
          <nav
            data-testid="portal-shell-nav"
            aria-label="Portal sections"
            className="w-full border-t border-neutral-100 pt-2"
          >
            <ul className="flex flex-wrap gap-x-4 gap-y-1">
              <li>
                <Link
                  href={base}
                  data-testid="portal-shell-nav-home"
                  aria-current={section === null ? "page" : undefined}
                  className={LINK_CLASS}
                >
                  Home
                </Link>
              </li>
              {navSlots.map((slot) => (
                <li key={slot.id}>
                  <Link
                    href={portalSectionHref(base, slot.id)}
                    data-testid={`portal-shell-nav-${slot.id}`}
                    aria-current={section === slot.id ? "page" : undefined}
                    className={LINK_CLASS}
                  >
                    {slot.label}
                  </Link>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </div>
    </header>
  )
}
