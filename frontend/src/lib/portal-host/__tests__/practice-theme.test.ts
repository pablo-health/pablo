// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { existsSync, readFileSync } from "fs"
import { join } from "path"
import { describe, expect, it, vi } from "vitest"
import { portalPracticeHost } from "../practice-host-request"
import type { PracticeHostAnswer } from "../practice-host"
import { PRACTICE_FONTS, type PracticeTheme, parsePracticeTheme, practiceThemeCss } from "../practice-theme"

const REPO = join(__dirname, "..", "..", "..", "..", "..")

const FULL = {
  version: 1,
  colors: {
    accent: "#24504c",
    accentText: "#ffffff",
    background: "#fbf8f3",
    surface: "#ffffff",
    text: "#1d2726",
    mutedText: "#55605e",
  },
  fonts: { heading: "Fraunces", body: "Inter" },
  radius: "md",
}

describe("parsePracticeTheme", () => {
  it("reads a whole theme", () => {
    expect(parsePracticeTheme(FULL)).toEqual({ colors: FULL.colors, fonts: FULL.fonts, radius: "md" })
  })

  it("keeps only values of the agreed shape", () => {
    const theme = parsePracticeTheme({
      colors: { accent: "#24504C", text: "red", surface: "#fff", background: "#000000;color:red", mutedText: null },
      fonts: { heading: "Comic Sans", body: "Inter", toString: "Inter" },
      radius: "huge",
    })

    expect(theme).toEqual({ colors: { accent: "#24504c" }, fonts: { body: "Inter" }, radius: null })
  })

  it.each([null, undefined, "theme", 3, [], {}, { colors: { accent: "nope" } }, { fonts: [] }])(
    "is no theme when nothing is usable: %j",
    (value) => {
      expect(parsePracticeTheme(value)).toBeNull()
    },
  )

  it("never takes a font the app does not serve, even one named like a property", () => {
    expect(parsePracticeTheme({ fonts: { body: "constructor", heading: "__proto__" } })).toBeNull()
  })
})

describe("practiceThemeCss", () => {
  const theme = parsePracticeTheme(FULL) as PracticeTheme
  const css = practiceThemeCss(theme)

  it("sets the portal's tokens on the themed element", () => {
    expect(css).toContain("[data-practice-theme]{")
    for (const declaration of [
      "--primary:#24504c",
      "--color-primary-600:#24504c",
      "--primary-foreground:#ffffff",
      "--color-neutral-50:#fbf8f3",
      "--color-white:#ffffff",
      "--color-neutral-900:#1d2726",
      "color:#1d2726",
      "--muted-foreground:#55605e",
      "--radius:0.625rem",
      '--font-display:"Fraunces", Georgia, serif',
      'font-family:"Inter", system-ui, sans-serif',
    ]) {
      expect(css).toContain(declaration)
    }
  })

  it("loads each font once, from this app", () => {
    const faces = css.match(/@font-face\{[^}]*\}/g) ?? []
    expect(faces).toHaveLength(2)
    expect(css).toContain('src:url("/fonts/practice/fraunces.woff2") format("woff2")')
    expect(css).toContain('src:url("/fonts/practice/inter.woff2") format("woff2")')
    expect(css).not.toMatch(/url\("?https?:/)

    const one = practiceThemeCss({ colors: {}, fonts: { heading: "Inter", body: "Inter" }, radius: null })
    expect(one.match(/@font-face/g)).toHaveLength(1)
  })

  it("sets only what the theme gives", () => {
    const accentOnly = practiceThemeCss({ colors: { accent: "#24504c" }, fonts: {}, radius: null })
    expect(accentOnly).toBe("[data-practice-theme]{--primary:#24504c;--color-primary-600:#24504c;--ring:#24504c}")
  })
})

describe("the fonts a theme can name", () => {
  it("are the backend's list, no more and no fewer", () => {
    // Two lists in two languages; read the backend's literal rather than a copy.
    const source = readFileSync(join(REPO, "backend", "app", "sites", "theme.py"), "utf8")
    const block = source.match(/^FONTS: tuple\[str, \.\.\.\] = \(([\s\S]*?)^\)/m)
    expect(block, "backend/app/sites/theme.py defines FONTS").toBeTruthy()
    const names = [...(block as RegExpMatchArray)[1].matchAll(/"([^"]+)"/g)].map((m) => m[1])
    expect([...names].sort()).toEqual(Object.keys(PRACTICE_FONTS).sort())
  })

  it("are each served from this app, with their licence beside them", () => {
    for (const { file } of Object.values(PRACTICE_FONTS)) {
      const folder = join(REPO, "frontend", "public", "fonts", "practice")
      expect(existsSync(join(folder, `${file}.woff2`)), `${file}.woff2`).toBe(true)
      expect(readFileSync(join(folder, `${file}-OFL.txt`), "utf8")).toContain("SIL Open Font License")
    }
  })
})

describe("portalPracticeHost", () => {
  const ENV = { APP_HOSTS: "app.example.com", PORTAL_HOSTS: "portal.example.com" }
  const THEME = parsePracticeTheme(FULL)
  const answering = (answer: PracticeHostAnswer) => vi.fn(async () => answer)

  it("is the practice's own host, with its theme", async () => {
    const found = { slug: "acme", primaryHost: null, theme: THEME, siteHost: null }
    const lookup = answering(found)

    expect(await portalPracticeHost("clients.acme-therapy.com", "acme", lookup, ENV)).toEqual(found)
    expect(lookup).toHaveBeenCalledWith("clients.acme-therapy.com")
  })

  it("is the practice's own host even when it has no theme", async () => {
    const found = { slug: "acme", primaryHost: null, theme: null, siteHost: null }

    expect(await portalPracticeHost("clients.acme-therapy.com", "acme", answering(found), ENV)).toEqual(found)
  })

  it.each([
    ["the app's own host", "app.example.com"],
    ["the shared portal host", "portal.example.com"],
    ["a local address", "localhost:3000"],
    ["no host at all", null],
  ])("is not on %s, which is never looked up", async (_name, host) => {
    const lookup = answering({ slug: "acme", primaryHost: null, theme: THEME, siteHost: null })

    expect(await portalPracticeHost(host, "acme", lookup, ENV)).toBeNull()
    expect(lookup).not.toHaveBeenCalled()
  })

  it.each([
    ["serves nothing", null],
    ["could not be asked", "unavailable"],
    ["is another practice's", { slug: "other", primaryHost: null, theme: THEME, siteHost: null }],
  ] as const)("is not when the host %s", async (_name, answer) => {
    expect(await portalPracticeHost("clients.acme-therapy.com", "acme", answering(answer), ENV)).toBeNull()
  })

  it("is not where practice hosts are not looked up at all", async () => {
    const lookup = answering({ slug: "acme", primaryHost: null, theme: THEME, siteHost: null })

    expect(await portalPracticeHost("clients.acme-therapy.com", "acme", lookup, {})).toBeNull()
  })
})
