// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where in the portal an address points, on any of the three forms it is
 * served at.
 *
 * On a shared host the portal lives at `/portal/{slug}/...`; on a portal
 * host of its own (`@/lib/portal-host/routing`) the same pages answer at
 * `/{slug}/...`, and `/portal/...` still works there too; on a practice's own
 * host (`@/lib/portal-host/practice-host`) they answer at the root, `/...`.
 * The shell builds its links from the form the patient is already on, so
 * moving between Home and a section never changes which form is in the
 * address bar.
 */

export interface PortalLocation {
  /** Home: `/portal/{slug}`, `/{slug}` or `/`, whichever form the path used. */
  base: string
  /** The section after the slug, or `null` on Home. */
  section: string | null
}

function decode(segment: string): string {
  try {
    return decodeURIComponent(segment)
  } catch {
    return segment
  }
}

/**
 * Read `pathname` as a portal address for `slug`.
 *
 * A path on neither slug form is the root form. The shell is only ever
 * served on one of the three, and on a practice's own host the proxy moves
 * `/{slug}/...` and `/portal/{slug}/...` to the root form, so the slug never
 * appears in the path there.
 */
export function portalLocation(slug: string, pathname: string): PortalLocation {
  const segments = pathname.split("/").filter(Boolean).map(decode)
  const encoded = encodeURIComponent(slug)
  if (segments[0] === "portal" && segments[1] === slug) {
    return { base: `/portal/${encoded}`, section: segments[2] ?? null }
  }
  if (segments[0] === slug) {
    return { base: `/${encoded}`, section: segments[1] ?? null }
  }
  return { base: "/", section: segments[0] ?? null }
}

/** The address of one section, on the same form as `base`. */
export function portalSectionHref(base: string, section: string): string {
  return `${base === "/" ? "" : base}/${encodeURIComponent(section)}`
}
