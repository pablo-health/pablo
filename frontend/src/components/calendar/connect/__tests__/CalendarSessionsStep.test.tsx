// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { readFileSync } from "node:fs"
import { join } from "node:path"
import { describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import type { GoogleCalendarSelection, GoogleCalendarStatus } from "@/lib/api/scheduling"
import { CalendarSessionsStep } from "../CalendarSessionsStep"

// Deliberately not the real wording: whatever the API sends is what shows.
const SERVED_STATEMENT = "I confirm the wording the server sent for this test."

const STATUS: GoogleCalendarStatus = {
  connected: false,
  calendar_id: null,
  calendar_name: null,
  last_synced_at: null,
  write_target: null,
  event_titling: null,
  titling_needs_attestation: false,
  titling_attestation_statement: SERVED_STATEMENT,
}

const FULL: GoogleCalendarSelection = {
  write_target: "app_calendar",
  busy: true,
  event_titling: "full",
}

function renderStep(status: GoogleCalendarStatus, selection: GoogleCalendarSelection = FULL) {
  return render(
    <CalendarSessionsStep
      step={2}
      status={status}
      options={undefined}
      selection={selection}
      onSelectionChange={vi.fn()}
      connecting={false}
      error={null}
      onConnect={vi.fn()}
      onSaveTitling={vi.fn()}
      attested={false}
      onAttestedChange={vi.fn()}
    />
  )
}

describe("CalendarSessionsStep full-name confirmation", () => {
  it("shows the statement the API sends, which is the one it records", () => {
    renderStep(STATUS)

    expect(screen.getByRole("checkbox", { name: SERVED_STATEMENT })).toBeInTheDocument()
  })

  it("does not offer a confirmation it has no wording for", () => {
    renderStep({ ...STATUS, titling_attestation_statement: undefined })

    expect(screen.queryByText(/business associate agreement/i)).toBeNull()
    expect(screen.queryByText(SERVED_STATEMENT)).toBeNull()
  })

  it("only asks for it when full names are chosen", () => {
    renderStep(STATUS, { ...FULL, event_titling: "initials" })

    expect(screen.queryByText(SERVED_STATEMENT)).toBeNull()
  })

  it("makes no claim about Gmail accounts that nothing checks", () => {
    renderStep(STATUS)

    expect(screen.queryByText(/gmail/i)).toBeNull()
  })

  it("carries no copy of the statement of its own", () => {
    // A second copy here is how the screen and the audit record disagreed
    // before: the wording belongs to the backend's versioned table.
    const source = readFileSync(join(__dirname, "..", "CalendarSessionsStep.tsx"), "utf8")

    expect(source).not.toMatch(/I confirm/)
  })
})
