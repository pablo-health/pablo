// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Client-side helpers for the companion deep-link handoff.
 *
 * The primary handoff is a domain-verified link (`https://<host>/launch/<id>`,
 * a Universal Link on macOS / App URI Handler on Windows). The legacy
 * `pablohealth://` custom scheme remains as the fallback for browsers that
 * don't honor verified links (Firefox) and as the route the in-page
 * `/launch/[intentId]` fallback fires. See docs/url-scheme.md and
 * docs/design/companion-thin-client.md.
 */

/**
 * Legacy custom-scheme launch URI carrying a launch intent. The companion
 * redeems the `intent` server-side (single-use, 180s TTL) — it does NOT
 * trust a raw appointment id when an intent is present.
 */
export function legacyLaunchUri(intentId: string): string {
  return `pablohealth://session/start?intent=${encodeURIComponent(intentId)}`
}

/**
 * Navigate via a real, user-activated anchor click.
 *
 * macOS Safari only routes a Universal Link when the navigation originates
 * from an actual anchor click inside the user gesture — a bare
 * `window.location` assignment is NOT routed to the companion. So when we
 * must fetch the launch intent on click (rather than pre-rendering the
 * anchor), we synthesize a real `<a>` and click it synchronously, still
 * inside the gesture handler.
 */
export function clickThroughAnchor(href: string): void {
  if (typeof document === "undefined") return
  const a = document.createElement("a")
  a.href = href
  a.rel = "noopener"
  // Keep it out of the layout/flow; it only exists to be clicked.
  a.style.display = "none"
  document.body.appendChild(a)
  a.click()
  a.remove()
}

/**
 * Arm a "no-handoff" fallback. If the OS hands off to the companion, the
 * page is backgrounded (visibilitychange / pagehide / blur fires) and we
 * cancel. If the timer elapses with the page still in the foreground (no
 * companion claimed the link — e.g. Firefox, or nothing installed), we run
 * `onNoHandoff` to fall back to the legacy scheme.
 *
 * Returns a cleanup function the caller should invoke on unmount.
 */
export function armNoHandoffFallback(
  onNoHandoff: () => void,
  delayMs = 1500,
): () => void {
  if (typeof window === "undefined") return () => {}

  let settled = false
  const cancel = () => {
    if (settled) return
    settled = true
    cleanup()
  }

  const timer = window.setTimeout(() => {
    if (settled) return
    settled = true
    cleanup()
    // Still here, still visible → the OS did not hand off.
    if (document.visibilityState === "visible") onNoHandoff()
  }, delayMs)

  const onVisibility = () => {
    if (document.visibilityState === "hidden") cancel()
  }

  function cleanup() {
    window.clearTimeout(timer)
    document.removeEventListener("visibilitychange", onVisibility)
    window.removeEventListener("pagehide", cancel)
    window.removeEventListener("blur", cancel)
  }

  document.addEventListener("visibilitychange", onVisibility)
  window.addEventListener("pagehide", cancel)
  window.addEventListener("blur", cancel)

  return cleanup
}
