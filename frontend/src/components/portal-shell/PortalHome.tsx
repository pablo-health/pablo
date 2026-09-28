// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The portal's home screen: the practice's welcome, then one tile per
 * section this practice serves, in registration order.
 *
 * A tile is a link to its section with the section's name and, where the
 * module registered one, a single line of where it stands. No section's own
 * content renders here — Home says what there is and lets the patient pick.
 *
 * The welcome is the practice's words from the capability document, shown as
 * plain text with its line breaks kept. Until that document is here the card
 * is a skeleton, never a stand-in sentence that would change under the
 * patient a moment later. A server too old to send a welcome gets the
 * practice's name in a heading and no body; a document that never arrives
 * gets no card at all, and the tiles still work.
 */

"use client"

import Link from "next/link"
import { ChevronRight } from "lucide-react"
import { portalSectionHref } from "@/lib/portal-shell/paths"
import { CardShell } from "./PortalAuthCards"
import { type PortalView, usePortalView } from "./context"
import type { PortalSlot } from "./slots"

export function PortalHome() {
  const view = usePortalView()

  if (view.capabilities.status === "loading") return <HomeSkeleton />

  return (
    <div data-testid="portal-home" className="flex flex-col gap-4">
      <WelcomeCard view={view} />
      {view.slots.length === 0 ? (
        // Two cases that look the same to the patient and should: nothing
        // is registered, and nothing this practice has turned on is. Neither
        // is an error, and saying which would be describing the deployment
        // to somebody who cannot act on it.
        <CardShell testId="portal-shell-empty">
          <p className="py-4 text-center text-sm text-neutral-600">
            Nothing here yet — your practice will send you anything they need.
          </p>
        </CardShell>
      ) : (
        <ul className="flex flex-col gap-3" aria-label="Sections">
          {view.slots.map((slot) => (
            <li key={slot.id}>
              <SectionTile slot={slot} view={view} />
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function WelcomeCard({ view }: { view: PortalView }) {
  if (view.capabilities.status !== "loaded") return null
  const { welcome, practice } = view.capabilities.data
  const name = practice.display_name ?? view.displayName
  const heading = welcome?.heading ?? (name ? `Welcome to ${name}` : null)
  if (heading === null) return null
  return (
    <CardShell testId="portal-home-welcome">
      <h2
        data-testid="portal-home-welcome-heading"
        className="whitespace-pre-line text-lg font-semibold text-neutral-900"
      >
        {heading}
      </h2>
      {welcome?.body ? (
        <p
          data-testid="portal-home-welcome-body"
          className="mt-2 whitespace-pre-line text-sm text-neutral-600"
        >
          {welcome.body}
        </p>
      ) : null}
    </CardShell>
  )
}

function SectionTile({ slot, view }: { slot: PortalSlot; view: PortalView }) {
  const { Summary } = slot
  return (
    <Link
      href={portalSectionHref(view.base, slot.id)}
      data-testid={`portal-home-tile-${slot.id}`}
      className="flex items-center justify-between gap-3 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm hover:border-neutral-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-neutral-900"
    >
      <span className="flex min-w-0 flex-col gap-1">
        <span className="text-base font-medium text-neutral-900">{slot.label ?? slot.id}</span>
        {Summary ? (
          <span data-testid={`portal-home-tile-${slot.id}-summary`} className="text-sm text-neutral-600">
            <Summary slug={view.slug} sessionToken={view.sessionToken} />
          </span>
        ) : null}
      </span>
      <ChevronRight className="h-5 w-5 shrink-0 text-neutral-400" aria-hidden="true" />
    </Link>
  )
}

function HomeSkeleton() {
  return (
    <div data-testid="portal-home-skeleton" className="flex flex-col gap-4" aria-busy="true">
      <div className="rounded-lg border border-neutral-200 bg-white p-6 shadow-sm">
        <div className="h-5 w-48 animate-pulse rounded bg-neutral-200" />
        <div className="mt-3 h-4 w-full animate-pulse rounded bg-neutral-100" />
        <div className="mt-2 h-4 w-3/4 animate-pulse rounded bg-neutral-100" />
      </div>
      <div className="h-16 animate-pulse rounded-lg bg-neutral-100" />
      <div className="h-16 animate-pulse rounded-lg bg-neutral-100" />
    </div>
  )
}
