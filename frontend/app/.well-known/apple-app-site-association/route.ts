// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Apple App Site Association (AASA).
 *
 * Served at `GET /.well-known/apple-app-site-association` (no `.json`
 * extension — Apple fetches the bare path). Declares that this domain's
 * `/launch/*` paths are owned by the companion app, so macOS routes the
 * verified Universal Link to the companion instead of opening the URL in
 * a browser tab. See docs/design/companion-thin-client.md.
 *
 * Values come exclusively from env so the same image serves both the dev
 * and prod hosts, and so self-hosters point it at their own signed build:
 *   AASA_TEAM_ID   — Apple Developer Team ID
 *   AASA_BUNDLE_ID — companion app bundle identifier
 * When either is unset the association is undefined for this deploy, so we
 * return 404 (Apple treats a missing file as "no association").
 *
 * `force-dynamic` + `runtime = "nodejs"`: the same frontend image is
 * promoted dev->prod, so the env must be read at REQUEST time, not frozen
 * into a statically-optimized response at build time (when the vars are
 * unset).
 */

import { NextResponse } from "next/server"

export const runtime = "nodejs"
export const dynamic = "force-dynamic"

export async function GET(): Promise<NextResponse> {
  const teamId = process.env.AASA_TEAM_ID
  const bundleId = process.env.AASA_BUNDLE_ID

  if (!teamId || !bundleId) {
    return new NextResponse(null, { status: 404 })
  }

  const body = {
    applinks: {
      apps: [],
      details: [
        {
          appIDs: [`${teamId}.${bundleId}`],
          components: [{ "/": "/launch/*" }],
        },
      ],
    },
  }

  return NextResponse.json(body, {
    headers: {
      // Apple accepts application/json; NextResponse.json sets this, but be
      // explicit since the path has no extension to infer the type from.
      "Content-Type": "application/json",
      "Cache-Control": "public, max-age=3600",
    },
  })
}
