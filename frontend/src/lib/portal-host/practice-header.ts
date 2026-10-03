// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The header a practice's portal shows on the practice's own host, so it
 * matches the practice's website: a wordmark, a subtitle, up to five links
 * and a call to action, from the `header` block of the live website's
 * `theme.json` (backend/app/sites/header.py).
 *
 * The backend has already checked every value. This side checks them again
 * with the same rules before any reaches the page, and drops whatever fails:
 * text is normalised and refused if it holds a control or invisible
 * character, mixes alphabets within a word, is empty, too long, or one of the
 * portal's own words; a link is a path on the website or an `https://`
 * address with no user name or port. Two rules are the backend's alone,
 * because only it knows their inputs: which hosts a full address may name
 * (the practice's own, checked again every time it serves the theme), and the
 * deployment's own name, which a label or wordmark may not take.
 *
 * Values are only ever rendered as React text and `href` attributes — never
 * into CSS (`./practice-theme`) and never as HTML.
 */

export const WORDMARK_MAX = 60
export const SUBTITLE_MAX = 80
export const LABEL_MAX = 24
export const HREF_MAX = 512
export const MAX_LINKS = 5

export interface HeaderLink {
  label: string
  /** An absolute `https://` address, ready for an `href`. */
  href: string
}

export interface PracticeHeader {
  wordmark: string | null
  subtitle: string | null
  links: HeaderLink[]
  cta: HeaderLink | null
}

/** Labels the portal uses itself (the backend's `RESERVED_LABELS`). */
const RESERVED_LABELS = new Set(["sign in", "log in", "login", "sign out"])

/** Controls, formatting characters (bidi controls among them), surrogates,
 * private use, unassigned, line and paragraph separators. */
const REFUSED = /[\p{Cc}\p{Cf}\p{Cs}\p{Co}\p{Cn}\p{Zl}\p{Zp}]/u
const LETTER = /[\p{Lu}\p{Ll}\p{Lt}\p{Lo}]/u
const OTHER_SCRIPT = "Other"
const SCRIPTS: readonly (readonly [string, RegExp])[] = [
  "Latin", "Greek", "Cyrillic", "Armenian", "Hebrew", "Arabic", "Syriac", "Thaana", "Devanagari",
  "Bengali", "Gurmukhi", "Gujarati", "Oriya", "Tamil", "Telugu", "Kannada", "Malayalam", "Sinhala",
  "Thai", "Lao", "Tibetan", "Myanmar", "Georgian", "Hangul", "Ethiopic", "Cherokee", "Khmer",
  "Mongolian", "Hiragana", "Katakana", "Bopomofo", "Han",
].map((name) => [name, new RegExp(`\\p{Script=${name}}`, "u")] as const)
/** Scripts written together in one word: Japanese, Korean, Chinese with annotation. */
const WRITTEN_TOGETHER: readonly ReadonlySet<string>[] = [
  new Set(["Han", "Hiragana", "Katakana"]),
  new Set(["Han", "Hangul"]),
  new Set(["Han", "Bopomofo"]),
]

function scriptOf(char: string): string | null {
  if (!LETTER.test(char)) return null
  return SCRIPTS.find(([, pattern]) => pattern.test(char))?.[0] ?? OTHER_SCRIPT
}

function mixesScripts(text: string): boolean {
  return text.split(" ").some((word) => {
    const scripts = new Set([...word].map(scriptOf).filter((s): s is string => s !== null))
    if (scripts.size <= 1) return false
    return !WRITTEN_TOGETHER.some((group) => [...scripts].every((s) => group.has(s)))
  })
}

function collapse(text: string): string {
  return text.split(/\s+/u).filter(Boolean).join(" ")
}

/** The same name, however it is spaced, cased or composed. */
export function sameName(a: string, b: string): boolean {
  const fold = (s: string) => collapse(s.normalize("NFKC")).toLowerCase()
  return fold(a) === fold(b)
}

/** `raw` normalised, or `null` when the backend's rules would refuse it. */
export function headerText(raw: unknown, cap: number): string | null {
  if (typeof raw !== "string") return null
  const normalised = raw.normalize("NFKC")
  if (REFUSED.test(normalised)) return null
  const text = collapse(normalised)
  if (!text || [...text].length > cap || mixesScripts(text)) return null
  return text
}

function label(raw: unknown): string | null {
  const text = headerText(raw, LABEL_MAX)
  return text !== null && !RESERVED_LABELS.has(text.toLowerCase()) ? text : null
}

const BAD_CHAR = /[\s\\\p{C}\p{Z}]/u
const HOSTNAME = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/

/** Percent-decoded once, as Python's `unquote` does: a stray `%` stays, bytes
 * that are not UTF-8 become U+FFFD. */
function percentDecoded(href: string): string {
  return href.replace(/(?:%[0-9a-fA-F]{2})+/g, (run) => {
    try {
      return decodeURIComponent(run)
    } catch {
      return "\ufffd"
    }
  })
}

function hasDotDot(path: string): boolean {
  return path.split("/").includes("..")
}

/**
 * Whether `href` is a path on the website or an `https://` address of the
 * right shape. Once `decoded`, a plain space (`%20`) is part of a path.
 */
function shapedLikeAPage(href: string, decoded: boolean): boolean {
  const allowed = decoded ? " " : ""
  if ([...href].some((c) => c !== allowed && BAD_CHAR.test(c))) return false
  if (href.startsWith("/")) {
    return !href.startsWith("//") && !hasDotDot(href.split("?")[0].split("#")[0])
  }
  if (href.slice(0, 8).toLowerCase() !== "https://") return false
  const authority = href.slice(8).split(/[/?#]/)[0]
  if (!authority || authority.includes("@") || authority.includes(":")) return false
  const host = authority.toLowerCase().replace(/\.$/, "")
  // The last label of a DNS name is never all digits; that is an IPv4 address.
  if (!HOSTNAME.test(host) || /\.\d+$/.test(host)) return false
  return !hasDotDot(href.slice(8 + authority.length).split("?")[0].split("#")[0])
}

/**
 * `raw` as an address to link to, or `null`: a path becomes an address on
 * the live website (`siteHost`), and is dropped when there is none.
 */
export function headerHref(raw: unknown, siteHost: string | null): string | null {
  if (typeof raw !== "string" || raw.length > HREF_MAX) return null
  if (!shapedLikeAPage(raw, false) || !shapedLikeAPage(percentDecoded(raw), true)) return null
  if (raw.startsWith("/")) return siteHost ? `https://${siteHost}${raw}` : null
  return raw
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

function link(value: unknown, siteHost: string | null): HeaderLink | null {
  const raw = record(value)
  if (!raw) return null
  const text = label(raw.label)
  const href = headerHref(raw.href, siteHost)
  return text !== null && href !== null ? { label: text, href } : null
}

/**
 * The header from the host lookup's theme, keeping only values that pass;
 * `null` when there is none or nothing in it is usable.
 */
export function parsePracticeHeader(value: unknown, siteHost: string | null): PracticeHeader | null {
  const raw = record(value)
  if (!raw) return null
  const wordmark = headerText(raw.wordmark, WORDMARK_MAX)
  const subtitle = headerText(raw.subtitle, SUBTITLE_MAX)
  const links: HeaderLink[] = []
  for (const item of Array.isArray(raw.links) ? raw.links : []) {
    const parsed = link(item, siteHost)
    if (parsed && links.length < MAX_LINKS && !links.some((kept) => kept.href === parsed.href)) links.push(parsed)
  }
  const cta = link(raw.cta, siteHost)
  if (wordmark === null && subtitle === null && links.length === 0 && cta === null) return null
  return { wordmark, subtitle, links, cta }
}
