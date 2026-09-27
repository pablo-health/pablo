// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { screen, fireEvent } from "@testing-library/react"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { ImportPreview, ImportRunDetail } from "@/lib/api/migration"
import { ImportReview, unanswered } from "../ImportReview"

vi.mock("@/lib/api/migration", async (orig) => ({
  ...(await orig<typeof import("@/lib/api/migration")>()),
  fetchImportFile: vi.fn().mockResolvedValue(new Blob(["%PDF"])),
}))

const card = (id: string, name: string, birthday: string | null) => ({
  card_id: id,
  display_name: name,
  folder_name: "Pablo Bear",
  birthday,
  email: `${id}@example.com`,
  phone: null,
  address: null,
  state: "new" as const,
  existing_patient_id: null,
  match_evidence: null,
  possible_duplicates: [],
})

function preview(): ImportPreview {
  return {
    source_system: "simplepractice",
    scope: "both",
    clients: [card("A", "Pablo A. Bear", "2025-01-01"), card("B", "Pablo Bear", "2025-03-01")],
    non_client_contacts: [],
    providers: ["Avery Provider"],
    records: [
      {
        record_type: "upload",
        source_id: "u1",
        path: "Stored documents/Pablo Bear/1-Sample upload.pdf",
        kind: "upload",
        label: "Sample upload.pdf",
        when: null,
        card_id: null,
        evidence: null,
        name_disagrees: false,
        candidates: ["A", "B"],
        state: "new",
        landable: true,
        reason: null,
      },
    ],
    counts: { note: { new: 4 }, contact: { new: 2 }, upload: { new: 1, unresolved: 1 } },
    cannot_land: [{ what: "billing", count: 5, reason: "Invoices land in a later version." }],
    questions: {
      same_name: [
        { folder_name: "Pablo Bear", candidates: ["A", "B"], records: [{ record_type: "upload", source_id: "u1" }] },
      ],
      duplicates: [],
      providers: [{ name: "Avery Provider" }],
    },
    practice: {
      proposals: { provider_name: null, visit_kind: null, visit_minutes: null, rate_cents: null },
      not_in_export: [],
    },
  }
}

function run(): ImportRunDetail {
  return {
    id: "run-1",
    source_system: "simplepractice",
    scope: "both",
    state: "previewed",
    started_at: "2026-09-27T12:00:00Z",
    finished_at: null,
    archive_expires_at: "2026-09-29T12:00:00Z",
    has_archive: true,
    counts: null,
    error: null,
    preview: preview(),
    decisions: null,
    report: null,
    missing: [],
  }
}

describe("ImportReview", () => {
  it("counts every open question", () => {
    const p = preview()
    expect(unanswered(p, { assignments: {}, duplicates: {}, providers: {}, practice: {} })).toBe(2)
    expect(
      unanswered(p, {
        assignments: { "upload:u1": "A" },
        duplicates: {},
        providers: { "Avery Provider": "me" },
        practice: {},
      }),
    ).toBe(0)
  })

  it("keeps Import off until every question is answered, then sends the answers", () => {
    const onApply = vi.fn()
    renderWithProviders(<ImportReview run={run()} applying={false} onApply={onApply} />)
    expect(screen.getByTestId("import-summary")).toHaveTextContent("This export will add 2 clients, 4 notes, 1 document.")
    expect(screen.getByText("Invoices land in a later version.")).toBeInTheDocument()
    const importButton = screen.getByRole("button", { name: "Import" })
    expect(importButton).toBeDisabled()
    expect(screen.getByText("2 questions to answer.")).toBeInTheDocument()

    fireEvent.click(screen.getByLabelText(/Pablo A\. Bear · born 2025-01-01/))
    fireEvent.click(screen.getByLabelText("Avery Provider is me"))
    expect(importButton).toBeEnabled()
    fireEvent.click(importButton)
    expect(onApply).toHaveBeenCalledWith({
      assignments: { "upload:u1": "A" },
      duplicates: {},
      providers: { "Avery Provider": "me" },
      practice: {},
    })
  })

  it("can leave a record behind instead of assigning it", () => {
    const onApply = vi.fn()
    renderWithProviders(<ImportReview run={run()} applying={false} onApply={onApply} />)
    fireEvent.click(screen.getByLabelText("Don't import this"))
    fireEvent.click(screen.getByLabelText("Avery Provider is me"))
    fireEvent.click(screen.getByRole("button", { name: "Import" }))
    expect(onApply.mock.calls[0][0].assignments).toEqual({ "upload:u1": "skip" })
  })

  it("says there is nothing new when a re-upload adds nothing", () => {
    const r = run()
    r.preview = { ...preview(), counts: { note: { unchanged: 4 } }, questions: { same_name: [], duplicates: [], providers: [] } }
    renderWithProviders(<ImportReview run={r} applying={false} onApply={vi.fn()} />)
    expect(screen.getByTestId("import-summary")).toHaveTextContent("Nothing new since your last import.")
    expect(screen.getByRole("button", { name: "Import" })).toBeDisabled()
  })
})
