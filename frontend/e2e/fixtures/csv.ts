// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Read an RFC 4180 CSV into records keyed by its header row.
 *
 * A field is quoted when it holds a comma, a quote or a line break, and a
 * quote inside one is doubled; rows end in CRLF (a bare LF is accepted). A
 * leading byte-order mark is dropped, since the export writes one for
 * spreadsheet applications.
 */
export function parseCsv(text: string): Record<string, string>[] {
  const rows: string[][] = [[]]
  let field = ""
  let quoted = false
  const body = text.startsWith("﻿") ? text.slice(1) : text
  for (let i = 0; i < body.length; i++) {
    const c = body[i]
    if (quoted) {
      if (c === '"' && body[i + 1] === '"') (field += '"'), i++
      else if (c === '"') quoted = false
      else field += c
    } else if (c === '"') quoted = true
    else if (c === ",") rows[rows.length - 1].push(field), (field = "")
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && body[i + 1] === "\n") i++
      rows[rows.length - 1].push(field)
      field = ""
      rows.push([])
    } else field += c
  }
  if (field !== "" || rows[rows.length - 1].length > 0) rows[rows.length - 1].push(field)
  const [header, ...records] = rows.filter((row) => row.length > 0)
  return records.map((row) => Object.fromEntries(header.map((name, i) => [name, row[i] ?? ""])))
}
