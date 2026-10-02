// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The theme a practice's portal wears on the practice's own host: a few colors,
 * a pair of fonts and a corner radius, taken from the `theme.json` of the
 * practice's live website (backend/app/sites/theme.py).
 *
 * The backend has already checked every value — hex colors, fonts from its
 * list, contrast held to WCAG AA — and this side checks the shape again before
 * a single value reaches CSS, so a theme can only ever set tokens. Layout,
 * spacing and everything else about the portal stay as they are.
 *
 * Fonts are served from this app (`public/fonts/practice/`), never fetched
 * from anywhere else: the Latin subset of each, as Google Fonts distributes
 * it, under the SIL Open Font License, whose text sits beside each file.
 * {@link PRACTICE_FONTS} must name the same fonts as the backend's `FONTS`; a
 * unit test fails when they differ.
 */

export const THEME_COLORS = ["accent", "accentText", "background", "surface", "text", "mutedText"] as const
export type ThemeColor = (typeof THEME_COLORS)[number]
export const THEME_RADII = ["none", "sm", "md", "lg"] as const
export type ThemeRadius = (typeof THEME_RADII)[number]

interface PracticeFont {
  file: string
  /** The weights the file holds, as `@font-face` takes them. */
  weight: string
  fallback: string
}

const SERIF = "Georgia, serif"
const SANS = "system-ui, sans-serif"

/** The fonts a theme can name, and the file each is served from. */
export const PRACTICE_FONTS: Readonly<Record<string, PracticeFont>> = {
  Fraunces: { file: "fraunces", weight: "100 900", fallback: SERIF },
  Newsreader: { file: "newsreader", weight: "200 800", fallback: SERIF },
  Inter: { file: "inter", weight: "100 900", fallback: SANS },
  "Hanken Grotesk": { file: "hanken-grotesk", weight: "100 900", fallback: SANS },
  "Nunito Sans": { file: "nunito-sans", weight: "200 1000", fallback: SANS },
  "Space Grotesk": { file: "space-grotesk", weight: "300 700", fallback: SANS },
  Poppins: { file: "poppins", weight: "400", fallback: SANS },
}

export interface PracticeTheme {
  colors: Partial<Record<ThemeColor, string>>
  fonts: { heading?: string; body?: string }
  radius: ThemeRadius | null
}

/**
 * The CSS variables each color sets. The portal draws with the app's palette,
 * so a theme color stands in for the tokens that play its part there:
 * surfaces are `white` and `card`, text is the dark end of the neutral scale.
 */
const COLOR_TOKENS: Record<ThemeColor, readonly string[]> = {
  accent: ["--primary", "--color-primary-600", "--ring"],
  accentText: ["--primary-foreground"],
  background: ["--background", "--color-neutral-50"],
  surface: ["--card", "--popover", "--color-white"],
  text: ["--foreground", "--card-foreground", "--popover-foreground", "--color-neutral-900", "--color-neutral-800", "--color-neutral-700"],
  mutedText: ["--muted-foreground", "--color-neutral-600", "--color-neutral-500"],
}

const RADIUS: Record<ThemeRadius, string> = { none: "0px", sm: "0.25rem", md: "0.625rem", lg: "1rem" }

const HEX = /^#[0-9a-fA-F]{6}$/

/** The element a theme is scoped to carries this attribute. */
export const THEME_ATTRIBUTE = "data-practice-theme"

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null
}

/**
 * A theme from the host lookup's answer, keeping only values of the agreed
 * shape; `null` when there is none or nothing in it is usable.
 */
export function parsePracticeTheme(value: unknown): PracticeTheme | null {
  const raw = record(value)
  if (!raw) return null
  const colors: PracticeTheme["colors"] = {}
  const rawColors = record(raw.colors) ?? {}
  for (const name of THEME_COLORS) {
    const color = rawColors[name]
    if (typeof color === "string" && HEX.test(color)) colors[name] = color.toLowerCase()
  }
  const fonts: PracticeTheme["fonts"] = {}
  const rawFonts = record(raw.fonts) ?? {}
  for (const role of ["heading", "body"] as const) {
    const font = rawFonts[role]
    if (typeof font === "string" && Object.hasOwn(PRACTICE_FONTS, font)) fonts[role] = font
  }
  const radius = THEME_RADII.find((r) => r === raw.radius) ?? null
  if (Object.keys(colors).length === 0 && Object.keys(fonts).length === 0 && radius === null) return null
  return { colors, fonts, radius }
}

function fontStack(name: string): string {
  return `"${name}", ${PRACTICE_FONTS[name].fallback}`
}

function fontFace(name: string): string {
  const font = PRACTICE_FONTS[name]
  return (
    `@font-face{font-family:"${name}";src:url("/fonts/practice/${font.file}.woff2") format("woff2");` +
    `font-weight:${font.weight};font-style:normal;font-display:swap}`
  )
}

/**
 * The stylesheet that puts `theme` on the element carrying
 * {@link THEME_ATTRIBUTE}. Every value in it comes from a parsed theme: a
 * six-digit hex color, a font from {@link PRACTICE_FONTS} or a radius from
 * {@link THEME_RADII} — nothing else is ever written into it.
 */
export function practiceThemeCss(theme: PracticeTheme): string {
  const declarations: string[] = []
  for (const name of THEME_COLORS) {
    const color = theme.colors[name]
    if (color) declarations.push(...COLOR_TOKENS[name].map((token) => `${token}:${color}`))
  }
  // Text and the font are inherited from the body, so they are set here as
  // well as through the tokens.
  if (theme.colors.text) declarations.push(`color:${theme.colors.text}`)
  if (theme.radius) declarations.push(`--radius:${RADIUS[theme.radius]}`)
  if (theme.fonts.heading) declarations.push(`--font-display:${fontStack(theme.fonts.heading)}`)
  if (theme.fonts.body) {
    declarations.push(`--font-sans:${fontStack(theme.fonts.body)}`, `font-family:${fontStack(theme.fonts.body)}`)
  }
  const faces = [...new Set([theme.fonts.heading, theme.fonts.body].filter((f) => f !== undefined))].map(fontFace)
  return [...faces, `[${THEME_ATTRIBUTE}]{${declarations.join(";")}}`].join("\n")
}
