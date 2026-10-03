// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Fails on a hard-coded "client(s)" or "patient(s)" a clinician would read.
 *
 * Therapists say clients and prescribers say patients, so the word comes from
 * `usePeopleTerm()` (see `src/lib/peopleTerm.ts`) rather than the source. This
 * walks every component and page, parses it, and flags the word in JSX text
 * and in string literals that read as prose (a space, or a leading capital).
 * Identifiers, URLs, type literals, object keys and comparisons are not text
 * and are left alone, which is why "patient" can stay in the code.
 *
 * When a string genuinely must keep the word (a billing code's official
 * descriptor, a field name stored on a clinical note), put
 * `people-term-ok: <reason>` in a comment on that line or the line above.
 */

import fs from "node:fs"
import path from "node:path"
import ts from "typescript"
import { describe, expect, it } from "vitest"

const ROOT = path.resolve(__dirname, "../../..")
const SCANNED = ["src", "app"]
const WORD = /\b(patients?|clients?)\b/i
const ALLOW = "people-term-ok"

/** Pages a client reads. They speak to the client as "you" and are not clinician copy. */
const CLIENT_FACING = [
  "app/portal/",
  "app/book/",
  "src/components/portal/",
  "src/components/portal-shell/",
  "src/components/booking/",
  "src/lib/portal-shell/",
  "src/lib/portal-host/",
  "src/lib/booking/",
]

/** Demo fixtures: transcripts and notes, not interface copy. */
const NOT_INTERFACE = ["src/lib/mockData.ts"]

/**
 * Files another change in flight is editing. Converting them here would
 * collide with it, so they wait for a follow-up once it lands, and this list
 * shrinks to empty. Do not add to it.
 */
const PENDING = [
  "app/(dashboard)/dashboard/calendar/page.tsx",
  "src/components/calendar/connect/",
  "src/components/calendar/editorial/EditorialCalendar.tsx",
  "src/components/calendar/editorial/EditorialDayView.tsx",
  "src/components/calendar/editorial/EditorialWeekView.tsx",
  "src/components/settings/FollowCalendarSetting.tsx",
  "src/components/settings/pages/AvailabilityPage.tsx",
]

const SKIPPED = [...CLIENT_FACING, ...NOT_INTERFACE, ...PENDING]

function sourceFiles(dir: string): string[] {
  const out: string[] = []
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name)
    if (entry.isDirectory()) {
      if (!["node_modules", "__tests__", "test", "e2e"].includes(entry.name)) out.push(...sourceFiles(full))
    } else if (/\.tsx?$/.test(entry.name) && !/\.(test|spec)\.tsx?$/.test(entry.name) && !entry.name.endsWith(".d.ts")) {
      out.push(full)
    }
  }
  return out
}

/** Positions where a string is code rather than words on a screen. */
function isCode(node: ts.Node): boolean {
  const parent = node.parent
  if (!parent) return false
  return (
    ts.isImportDeclaration(parent) ||
    ts.isExportDeclaration(parent) ||
    ts.isLiteralTypeNode(parent) ||
    (ts.isPropertyAssignment(parent) && parent.name === node) ||
    ts.isBinaryExpression(parent) ||
    ts.isCaseClause(parent) ||
    ts.isElementAccessExpression(parent) ||
    ts.isExpressionStatement(parent) || // "use client"
    // An Error's message is for the developer reading the log.
    (ts.isNewExpression(parent) && /Error$/.test(parent.expression.getText())) ||
    (ts.isCallExpression(parent) && parent.expression.kind === ts.SyntaxKind.SuperKeyword)
  )
}

const readsAsProse = (text: string) => /\s/.test(text.trim()) || /^[A-Z]/.test(text.trim())

export function findHardCodedPeopleWords(file: string, source: string): string[] {
  const sf = ts.createSourceFile(file, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const lines = source.split("\n")
  const hits: string[] = []

  const visit = (node: ts.Node) => {
    let text: string | null = null
    if (ts.isJsxText(node)) {
      text = node.getText()
    } else if ((ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) && !isCode(node)) {
      if (readsAsProse(node.text)) text = node.text
    } else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) {
      const template = ts.isTemplateHead(node) ? node.parent : node.parent.parent
      if (readsAsProse(node.text) && !isCode(template)) text = node.text
    }
    if (text && WORD.test(text) && !/\/patients?\b/.test(text)) {
      const line = sf.getLineAndCharacterOfPosition(node.getStart()).line
      const allowed = lines[line]?.includes(ALLOW) || lines[line - 1]?.includes(ALLOW)
      if (!allowed) hits.push(`${file}:${line + 1}: ${text.trim().replace(/\s+/g, " ").slice(0, 80)}`)
    }
    ts.forEachChild(node, visit)
  }
  visit(sf)
  return hits
}

describe("people term guard", () => {
  it("finds no hard-coded client/patient wording outside usePeopleTerm()", () => {
    const hits = SCANNED.flatMap((dir) => sourceFiles(path.join(ROOT, dir)))
      .map((full) => path.relative(ROOT, full).split(path.sep).join("/"))
      .filter((rel) => !SKIPPED.some((prefix) => rel.startsWith(prefix)))
      .flatMap((rel) => findHardCodedPeopleWords(rel, fs.readFileSync(path.join(ROOT, rel), "utf8")))
    expect(hits, "Use usePeopleTerm() for these (see src/lib/peopleTerm.ts):").toEqual([])
  })

  it("flags visible wording and leaves code alone", () => {
    const source = [
      'import { Patient } from "@/types/patients"',
      'const url = `/patients/${id}`',
      'if (kind === "patient") {}',
      "const label = <p>Add a client</p>",
      'const title = "Patients"',
      '// people-term-ok: official code descriptor',
      'const code = "Family session — patient present"',
    ].join("\n")
    expect(findHardCodedPeopleWords("x.tsx", source)).toEqual([
      "x.tsx:4: Add a client",
      "x.tsx:5: Patients",
    ])
  })

  it("lists only files that still exist as pending", () => {
    for (const prefix of PENDING) expect(fs.existsSync(path.join(ROOT, prefix)), prefix).toBe(true)
  })
})
