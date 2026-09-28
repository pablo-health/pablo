// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The shell's header: the practice's name, Sign out, and the navigation.
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
import type { PortalSlot } from "./slots"

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
  return (
    <header className="border-b border-neutral-200 bg-white px-4 py-4">
      <div className="mx-auto flex max-w-md flex-wrap items-center justify-between gap-x-4 gap-y-2">
        {displayName ? (
          <h1 data-testid="portal-shell-practice-name" className="text-base font-semibold">
            {displayName}
          </h1>
        ) : (
          <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
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
