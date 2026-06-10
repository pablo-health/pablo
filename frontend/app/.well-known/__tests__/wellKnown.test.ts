// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it } from "vitest"
import { GET as aasaGET } from "../apple-app-site-association/route"
import { GET as windowsGET } from "../windows-app-web-link/route"

const ORIGINAL_ENV = { ...process.env }

afterEach(() => {
  process.env = { ...ORIGINAL_ENV }
})

describe("apple-app-site-association route", () => {
  beforeEach(() => {
    delete process.env.AASA_TEAM_ID
    delete process.env.AASA_BUNDLE_ID
  })

  it("404s when env is unset", async () => {
    const res = await aasaGET()
    expect(res.status).toBe(404)
  })

  it("404s when only one of the two vars is set", async () => {
    process.env.AASA_TEAM_ID = "ABCDE12345"
    const res = await aasaGET()
    expect(res.status).toBe(404)
  })

  it("serves the association with appIDs from env", async () => {
    process.env.AASA_TEAM_ID = "ABCDE12345"
    process.env.AASA_BUNDLE_ID = "com.example.companion"

    const res = await aasaGET()
    expect(res.status).toBe(200)
    expect(res.headers.get("content-type")).toContain("application/json")
    expect(res.headers.get("cache-control")).toBe("public, max-age=3600")

    const body = await res.json()
    expect(body).toEqual({
      applinks: {
        apps: [],
        details: [
          {
            appIDs: ["ABCDE12345.com.example.companion"],
            components: [{ "/": "/launch/*" }],
          },
        ],
      },
    })
  })
})

describe("windows-app-web-link route", () => {
  beforeEach(() => {
    delete process.env.WINDOWS_APP_PFN
  })

  it("404s when the PFN env is unset", async () => {
    const res = await windowsGET()
    expect(res.status).toBe(404)
  })

  it("serves the package-family association from env", async () => {
    process.env.WINDOWS_APP_PFN = "Example.Companion_abcd1234"

    const res = await windowsGET()
    expect(res.status).toBe(200)
    expect(res.headers.get("content-type")).toContain("application/json")
    expect(res.headers.get("cache-control")).toBe("public, max-age=3600")

    const body = await res.json()
    expect(body).toEqual([
      { packageFamilyName: "Example.Companion_abcd1234", paths: ["/launch/*"] },
    ])
  })
})
