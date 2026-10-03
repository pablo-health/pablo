// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The parts of the shell's header that come from the practice's website, on
 * the practice's own host when its theme.json declares a header
 * (`@/lib/portal-host/practice-header`): the wordmark and subtitle, linking
 * to the website, its links and its call to action.
 *
 * The practice's name from its record stays the page's `<h1>`. When the
 * wordmark says the same thing it is shown once; when it says something else
 * the wordmark is what a visitor sees, and the record's name is still the
 * heading and the header's name for a screen reader (`ShellHeader` labels the
 * landmark with it).
 *
 * Every value is rendered as text or as an `href`, nothing else.
 */

"use client"

import { Button } from "@/components/ui/button"
import { type PracticeHeader, sameName } from "@/lib/portal-host/practice-header"

const NAME_CLASS = "font-display text-lg font-semibold"
const LINK_CLASS = "text-sm text-neutral-600 underline-offset-4 hover:underline focus-visible:underline"

function WebsiteLink({ href, className, children }: { href: string | null; className?: string; children: React.ReactNode }) {
  if (href === null) return <span className={className}>{children}</span>
  return (
    <a href={href} rel="noopener" data-testid="portal-header-wordmark" className={className}>
      {children}
    </a>
  )
}

export function PracticeBrand({
  displayName,
  header,
  siteHost,
}: {
  displayName: string | null
  header: PracticeHeader
  siteHost: string | null
}) {
  const siteRoot = siteHost ? `https://${siteHost}/` : null
  const wordmark = header.wordmark
  const showsOwnWordmark = wordmark !== null && (displayName === null || !sameName(wordmark, displayName))
  return (
    <div className="min-w-0">
      {showsOwnWordmark ? (
        <>
          {displayName && (
            <h1 data-testid="portal-shell-practice-name" className="sr-only">
              {displayName}
            </h1>
          )}
          <WebsiteLink href={siteRoot} className={`${NAME_CLASS} block`}>
            {wordmark}
          </WebsiteLink>
        </>
      ) : displayName ? (
        <h1 data-testid="portal-shell-practice-name" className={NAME_CLASS}>
          <WebsiteLink href={siteRoot}>{displayName}</WebsiteLink>
        </h1>
      ) : (
        <div className="h-5 w-40 animate-pulse rounded bg-neutral-200" aria-hidden="true" />
      )}
      {header.subtitle && (
        <p data-testid="portal-header-subtitle" className="text-xs text-neutral-600">
          {header.subtitle}
        </p>
      )}
    </div>
  )
}

export function WebsiteCta({ header }: { header: PracticeHeader }) {
  if (header.cta === null) return null
  return (
    <Button asChild size="sm" data-testid="portal-header-cta">
      <a href={header.cta.href} rel="noopener">
        {header.cta.label}
      </a>
    </Button>
  )
}

export function WebsiteLinks({ header }: { header: PracticeHeader }) {
  if (header.links.length === 0) return null
  return (
    <nav data-testid="portal-header-links" aria-label="Practice website" className="w-full">
      <ul className="flex flex-wrap gap-x-4 gap-y-1">
        {header.links.map((link) => (
          <li key={link.href}>
            <a href={link.href} rel="noopener" className={LINK_CLASS}>
              {link.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  )
}
