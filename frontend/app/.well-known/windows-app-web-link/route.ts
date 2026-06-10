// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Windows App URI Handler verification file ("Apps for Websites").
 *
 * Served at `GET /.well-known/windows-app-web-link`. Declares which MSIX
 * package family owns this domain's `/launch/*` paths, so Windows routes
 * the verified deep link to the companion's `<uap3:AppUriHandler>` instead
 * of a browser. See docs/design/companion-thin-client.md.
 *
 * The Windows well-known filename for App URI Handlers is
 * `windows-app-web-link` (NOT `web-credentials`, which is a different
 * Web Authentication mechanism). The package family name comes from env:
 *   WINDOWS_APP_PFN — the MSIX package family name
 * When unset the association is undefined for this deploy, so we return 404.
 *
 * `force-dynamic` + `runtime = "nodejs"`: env is read at REQUEST time so the
 * one promoted image serves both hosts (and self-hosters set their own PFN).
 */

import { NextResponse } from "next/server"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

export async function GET(): Promise<NextResponse> {
  const packageFamilyName = process.env.WINDOWS_APP_PFN

  if (!packageFamilyName) {
    return new NextResponse(null, { status: 404 })
  }

  const body = [{ packageFamilyName, paths: ["/launch/*"] }]

  return NextResponse.json(body, {
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=3600",
    },
  })
}
