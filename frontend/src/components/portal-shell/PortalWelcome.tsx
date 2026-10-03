// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The greeting above the signed-out cards. It says whose portal this is and
 * nothing about what is inside: what a practice offers here is the practice's
 * choice, and the visitor sees it once signed in.
 */

"use client"

export function PortalWelcome({ displayName }: { displayName: string | null }) {
  return (
    <section data-testid="portal-welcome" className="mb-6 sm:mb-8">
      {displayName ? (
        <h2 className="font-display text-2xl font-semibold text-neutral-900 sm:text-3xl">
          Welcome to {displayName}
        </h2>
      ) : (
        <div className="h-8 w-56 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
      )}
    </section>
  )
}
