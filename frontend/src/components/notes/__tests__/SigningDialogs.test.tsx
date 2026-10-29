// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The sign dialog shows its form only once what the form shows is known.
 * Before, the name field filled itself in a moment after opening (so text
 * typed into it meanwhile ended up appended to the profile's name), and the
 * button's label changed from "Sign and lock" to "Sign without updating" as
 * the chart updates arrived, under the click.
 */

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { SignNoteDialog } from "../signing/SigningDialogs"
import { createMockNote } from "@/test/factories"

const defaults = { name: "Sam Ortiz", credentials: "LMFT", loaded: true }
const updates = {
  proposals: [],
  diagnoses: [],
  undecided: 0,
  run: null,
  loaded: true,
}

vi.mock("@/hooks/useNoteSigning", () => ({
  useSignerDefaults: () => defaults,
}))
vi.mock("@/hooks/usePreferences", () => ({
  useUserTimeZone: () => "America/New_York",
}))
vi.mock("../chartUpdates/UpdateChart", () => ({
  useChartUpdates: () => updates,
  UpdateChart: () => <div data-testid="update-chart" />,
}))

afterEach(() => {
  Object.assign(defaults, {
    name: "Sam Ortiz",
    credentials: "LMFT",
    loaded: true,
  })
  Object.assign(updates, { undecided: 0, loaded: true })
})

function open(note?: ReturnType<typeof createMockNote>) {
  return render(<SignNoteDialog open onOpenChange={() => {}} onSign={async () => {}} note={note} />)
}

describe("SignNoteDialog", () => {
  it("shows the form once the signer's profile is known, with the name already in it", () => {
    Object.assign(defaults, { name: "", credentials: "", loaded: false })
    const view = open()
    expect(screen.queryByLabelText("Your name")).toBeNull()
    expect(screen.queryByRole("button", { name: /^Sign/ })).toBeNull()
    expect(screen.getByRole("status")).toHaveTextContent("Getting the note ready to sign")

    Object.assign(defaults, {
      name: "Sam Ortiz",
      credentials: "LMFT",
      loaded: true,
    })
    view.rerender(<SignNoteDialog open onOpenChange={() => {}} onSign={async () => {}} />)
    expect(screen.getByLabelText("Your name")).toHaveValue("Sam Ortiz")
    expect(screen.getByRole("button", { name: "Sign and lock" })).toBeEnabled()
  })

  it("labels the button only once the chart updates are known", () => {
    const note = createMockNote()
    Object.assign(updates, { undecided: 0, loaded: false })
    const view = open(note)
    expect(screen.queryByRole("button", { name: /^Sign/ })).toBeNull()
    expect(screen.queryByTestId("update-chart")).toBeNull()

    Object.assign(updates, { undecided: 2, loaded: true })
    view.rerender(
      <SignNoteDialog open onOpenChange={() => {}} onSign={async () => {}} note={note} />,
    )
    expect(screen.getByRole("button", { name: "Sign without updating" })).toBeEnabled()
    expect(screen.queryByRole("button", { name: "Sign and lock" })).toBeNull()
    expect(screen.getByTestId("update-chart")).toBeInTheDocument()
  })
})
