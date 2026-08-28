// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Where the browser should go after the native app has been handed the
 * authorization code. A custom-scheme redirect launches the app and leaves
 * this page running, so it navigates on to the dashboard itself. A loopback
 * redirect (http/https) sends the browser away from this page entirely —
 * there's nothing left for it to do.
 */
export function completionPathAfterHandoff(redirectUri: string): string | null {
  let url: URL
  try {
    url = new URL(redirectUri)
  } catch {
    return null
  }

  if (url.protocol === "http:" || url.protocol === "https:") return null
  return "/dashboard?from=companion"
}
