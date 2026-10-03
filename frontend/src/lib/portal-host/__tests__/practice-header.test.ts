// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { headerHref, headerText, parsePracticeHeader, sameName } from "../practice-header"

/**
 * The portal's second check of a website's header block. The cases mirror the
 * backend's (backend/tests/test_site_header.py): what passes there passes
 * here, and what it refuses never reaches the page.
 */

const SITE = "www.riverside.example"

describe("headerText", () => {
  it("normalises", () => {
    expect(headerText("  Riverside\u3000  Counseling ", 60)).toBe("Riverside Counseling")
    expect(headerText("\ufb01ne care", 60)).toBe("fine care")
  })

  it.each([
    "Riverside\x00",
    "Riverside\x85",
    "Riverside\nCounseling",
    "River\u202eside",
    "River\u2066side\u2069",
    "River\u200fside",
    "River\u061cside",
    "River\u200bside",
    "River\u00adside",
    "River\ue000side",
    "River\u{e0001}side",
    "River\u2028side",
    "River\u2029side",
    "River\u{10fffe}side",
  ])("refuses controls and invisible characters: %j", (text) => {
    expect(headerText(text, 60)).toBeNull()
  })

  it.each(["Riv\u0435rside", "\u03a1iverside", "Riverside \u0421\u043eunseling"])("refuses a word that mixes alphabets: %s", (text) => {
    expect(headerText(text, 60)).toBeNull()
  })

  it.each([
    "Riverside თერაპია",
    "東京カウンセリング",
    "ひだまり心療内科",
    "서울상담센터",
    "Café Ünïcödé",
    "Riverside 2.0 - Care & Co.",
    "O\u2019Brien Counseling",
  ])("passes one alphabet a word: %s", (text) => {
    expect(headerText(text, 60)).toBe(text)
  })

  it("refuses empty text and anything that is not text", () => {
    expect(headerText("   ", 60)).toBeNull()
    expect(headerText(7, 60)).toBeNull()
    expect(headerText(null, 60)).toBeNull()
  })

  it("caps in characters after normalising", () => {
    expect(headerText("W".repeat(61), 60)).toBeNull()
    expect(headerText(`${"W".repeat(29)}   ${"W".repeat(30)}  `, 60)).toHaveLength(60)
    // Characters, not UTF-16 units: four emoji are four.
    expect(headerText("\u{1f33f}\u{1f33f}\u{1f33f}\u{1f33f}", 4)).toBe("\u{1f33f}\u{1f33f}\u{1f33f}\u{1f33f}")
  })
})

describe("headerHref", () => {
  it.each([
    ["/", `https://${SITE}/`],
    ["/about", `https://${SITE}/about`],
    ["/#services", `https://${SITE}/#services`],
    ["/services/therapy?for=couples#fees", `https://${SITE}/services/therapy?for=couples#fees`],
    ["/a%20page", `https://${SITE}/a%20page`],
    ["/100%25-online", `https://${SITE}/100%25-online`],
    ["/%zz", `https://${SITE}/%zz`],
    ["https://riverside.example", "https://riverside.example"],
    ["https://WWW.Riverside.Example./about", "https://WWW.Riverside.Example./about"],
    ["HTTPS://riverside.example/about", "HTTPS://riverside.example/about"],
  ])("links %s", (href, expected) => {
    expect(headerHref(href, SITE)).toBe(expected)
  })

  it.each([
    "about",
    "#services",
    "riverside.example/about",
    "//riverside.example/about",
    "/\\evil.example",
    "/../admin",
    "/a/../../b",
    "/%2e%2e/admin",
    "/%2F%2Fevil.example",
    "/a b",
    "/a\tb",
    "/a%0Ab",
    "http://riverside.example/",
    "https://user@riverside.example/",
    "https://riverside.example:8443/",
    "https:///about",
    "https://1.2.3.4./",
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html,<script>alert(1)</script>",
    "mailto:hello@riverside.example",
    "tel:+15555550100",
    "blob:https://riverside.example/1234",
    "",
    `/${"a".repeat(512)}`,
  ])("refuses %j", (href) => {
    expect(headerHref(href, SITE)).toBeNull()
  })

  it("drops a path when there is no live website to put it on", () => {
    expect(headerHref("/about", null)).toBeNull()
    expect(headerHref("https://riverside.example/about", null)).toBe("https://riverside.example/about")
  })
})

describe("parsePracticeHeader", () => {
  const HEADER = {
    wordmark: "Riverside Counseling",
    subtitle: "Individual and couples therapy",
    links: [
      { label: "Services", href: "/#services" },
      { label: "Fees", href: "https://riverside.example/fees" },
    ],
    cta: { label: "Schedule a visit", href: "/#schedule" },
  }

  it("reads a header, its paths made addresses on the live website", () => {
    expect(parsePracticeHeader(HEADER, SITE)).toEqual({
      wordmark: "Riverside Counseling",
      subtitle: "Individual and couples therapy",
      links: [
        { label: "Services", href: `https://${SITE}/#services` },
        { label: "Fees", href: "https://riverside.example/fees" },
      ],
      cta: { label: "Schedule a visit", href: `https://${SITE}/#schedule` },
    })
  })

  it("drops each value that fails and keeps the rest", () => {
    const parsed = parsePracticeHeader(
      {
        wordmark: "Riv\u0435rside",
        subtitle: 3,
        links: [
          { label: "Sign in", href: "/signin" },
          { label: "Bad", href: "javascript:alert(1)" },
          { label: "S\u043eon", href: "/soon" },
          { label: "About" },
          "About",
          { label: "About", href: "/about" },
          { label: "Me", href: "/about" },
        ],
        cta: { label: "Book", href: "https://evil.example:444/" },
      },
      SITE,
    )

    expect(parsed).toEqual({
      wordmark: null,
      subtitle: null,
      links: [{ label: "About", href: `https://${SITE}/about` }],
      cta: null,
    })
  })

  it("keeps at most five links", () => {
    const links = Array.from({ length: 7 }, (_, i) => ({ label: `Page ${i}`, href: `/${i}` }))

    expect(parsePracticeHeader({ links }, SITE)?.links.map((link) => link.label)).toEqual([
      "Page 0",
      "Page 1",
      "Page 2",
      "Page 3",
      "Page 4",
    ])
  })

  it("is null when there is no header or nothing in it is usable", () => {
    expect(parsePracticeHeader(null, SITE)).toBeNull()
    expect(parsePracticeHeader(["x"], SITE)).toBeNull()
    expect(parsePracticeHeader({ wordmark: "", links: "x", cta: { label: "Go", href: "/go" } }, null)).toBeNull()
  })
})

describe("sameName", () => {
  it("ignores case, spacing and composition", () => {
    expect(sameName("Riverside  Counseling", "riverside counseling")).toBe(true)
    expect(sameName("Café", "Cafe\u0301")).toBe(true)
    expect(sameName("Riverside Counseling", "Riverside Counseling, LLC")).toBe(false)
  })
})
