// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { execFileSync } from "node:child_process"
import { mkdtempSync, readFileSync, rmSync } from "node:fs"
import { tmpdir } from "node:os"
import path from "node:path"
import { describe, expect, it } from "vitest"

// The committed types are what `npm run gen:api` makes from the committed
// schema. The backend's test_openapi_schema_published.py keeps that schema in
// step with the routes, so together they keep these types in step with the API.
const FRONTEND = path.resolve(__dirname, "../../../..")

describe("schema.gen.ts", () => {
  it("is what gen:api generates from openapi.json", () => {
    const out = mkdtempSync(path.join(tmpdir(), "gen-api-"))
    try {
      const target = path.join(out, "schema.gen.ts")
      const script = JSON.parse(readFileSync(path.join(FRONTEND, "package.json"), "utf-8")).scripts[
        "gen:api"
      ] as string
      const args = script
        .replace(/^openapi-typescript /, "")
        .replace("src/types/api/schema.gen.ts", target)
        .split(" ")
      execFileSync(path.join(FRONTEND, "node_modules/.bin/openapi-typescript"), args, {
        cwd: FRONTEND,
        stdio: "ignore",
      })
      const committed = readFileSync(path.join(FRONTEND, "src/types/api/schema.gen.ts"), "utf-8")
      expect(readFileSync(target, "utf-8") === committed, "Run `make api-schema` and commit the result.").toBe(
        true
      )
    } finally {
      rmSync(out, { recursive: true, force: true })
    }
  }, 60_000)
})
