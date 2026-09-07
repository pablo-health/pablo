// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { ApiError } from "../fixtures/api"
import { test, expect } from "../fixtures/auth"
import { givePatient, giveScheduledSession } from "../fixtures/scenarios"

type Session = { note: { note_type: string; content: Record<string, unknown> | null } | null }

test.describe("scheduled session note types", () => {
  for (const expected of ["narrative", "soap"] as const) {
    test(`${expected} is stored on the preallocated note`, async ({ api }) => {
      const patient = await givePatient(api)
      const created = await giveScheduledSession(api, patient.id, expected === "soap" ? undefined : expected)
      const session = await api.get<Session>(`/api/sessions/${created.id}`)
      expect(session.note?.note_type).toBe(expected)
      expect(session.note?.content ?? null).toBeNull()
    })
  }

  test("an unknown note type is rejected", async ({ api }) => {
    const patient = await givePatient(api)
    let error: unknown
    try {
      await giveScheduledSession(api, patient.id, "not-a-real-type")
    } catch (caught) {
      error = caught
    }
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 400 })
    expect((error as ApiError).body).toContain("INVALID_NOTE_TYPE")
  })
})
