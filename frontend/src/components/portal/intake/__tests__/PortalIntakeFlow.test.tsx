// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient portal intake form, end to end through its four steps.
 *
 * The API module is mocked with `importOriginal`, so `PatientIntakeError`
 * stays the real class — the component branches on it. Two cases deliberately
 * leave the real client in place and stub `fetch` instead, because what a 401
 * and a 403 do to the screen is the thing worth proving.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { UserEvent } from "@testing-library/user-event"
import { PortalIntakeFlow } from "../PortalIntakeFlow"
import { CRISIS_FOOTER } from "../intakeCopy"
import * as api from "@/lib/api/patientIntake"
import {
  GAD7_ITEM_COUNT,
  INTAKE_FORM,
  INTAKE_SUBMISSION,
  PHQ9_ITEM_COUNT,
} from "./intakeFormFixture"

vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  return {
    ...actual,
    fetchIntakeForm: vi.fn(actual.fetchIntakeForm),
    submitIntake: vi.fn(actual.submitIntake),
  }
})

const TOKEN = "portal-session-token"

/** A response object, rather than the environment's `Response`, so the test
 * asserts on our own status handling and not on a DOM polyfill. */
function jsonResponse(status: number, body: unknown): Response {
  return { ok: status >= 200 && status < 300, status, json: async () => body } as Response
}

async function renderReady(): Promise<UserEvent> {
  vi.mocked(api.fetchIntakeForm).mockResolvedValue(INTAKE_FORM)
  vi.mocked(api.submitIntake).mockResolvedValue(INTAKE_SUBMISSION)
  const user = userEvent.setup()
  render(<PortalIntakeFlow sessionToken={TOKEN} />)
  await screen.findByTestId("intake-flow")
  return user
}

function itemGroup(code: string, key: string): HTMLElement {
  return screen.getByTestId(`intake-item-${code}-${key}`)
}

async function answerItem(user: UserEvent, code: string, key: string, label: string) {
  await user.click(within(itemGroup(code, key)).getByRole("radio", { name: label }))
}

async function answerAll(user: UserEvent, code: string, count: number, label: string) {
  for (let i = 1; i <= count; i += 1) {
    await answerItem(user, code, String(i), label)
  }
}

/** Identity confirmed, a reason typed, standing on the PHQ-9 step. */
async function advanceToScreener(user: UserEvent, reason = "Sleep has been bad") {
  await user.click(screen.getByTestId("intake-identity-confirm"))
  await user.click(screen.getByTestId("intake-continue"))
  await user.type(screen.getByTestId("intake-reason"), reason)
  await user.click(screen.getByTestId("intake-continue"))
}

/** Both screeners answered with the same anchor, standing on the submit button. */
async function advanceToSubmit(user: UserEvent, label = "Not at all") {
  await answerAll(user, "phq9", PHQ9_ITEM_COUNT, label)
  await user.click(screen.getByTestId("intake-continue"))
  await answerAll(user, "gad7", GAD7_ITEM_COUNT, label)
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.unstubAllGlobals()
})

describe("PortalIntakeFlow", () => {
  it("renders the identity, reason, PHQ-9 and GAD-7 steps from the form response", async () => {
    const user = await renderReady()

    expect(screen.getByTestId("intake-progress")).toHaveTextContent("Step 1 of 4")
    expect(screen.getByTestId("intake-identity-name")).toHaveTextContent("Dana Okonkwo")
    expect(screen.getByTestId("intake-identity-dob")).toHaveTextContent("1988-04-02")

    await user.click(screen.getByTestId("intake-identity-confirm"))
    await user.click(screen.getByTestId("intake-continue"))
    expect(screen.getByRole("heading", { name: "What brings you in?" })).toBeInTheDocument()

    await user.type(screen.getByTestId("intake-reason"), "Sleep has been bad")
    await user.click(screen.getByTestId("intake-continue"))
    expect(screen.getByRole("heading", { name: "PHQ-9" })).toBeInTheDocument()
    expect(screen.getByText(/little interest or pleasure/i)).toBeInTheDocument()

    await answerAll(user, "phq9", PHQ9_ITEM_COUNT, "Not at all")
    await user.click(screen.getByTestId("intake-continue"))
    expect(screen.getByRole("heading", { name: "GAD-7" })).toBeInTheDocument()
    expect(screen.getByText(/feeling nervous, anxious, or on edge/i)).toBeInTheDocument()
    expect(screen.getByTestId("intake-progress")).toHaveTextContent("Step 4 of 4")
  })

  it("gives every item a four-radio group and holds Continue until the step is complete", async () => {
    const user = await renderReady()
    await advanceToScreener(user)

    const firstItem = itemGroup("phq9", "1")
    expect(firstItem.tagName).toBe("FIELDSET")
    expect(within(firstItem).getAllByRole("radio")).toHaveLength(4)
    expect(
      screen.getByRole("group", { name: /little interest or pleasure in doing things/i }),
    ).toBe(firstItem)

    expect(screen.getByTestId("intake-continue")).toBeDisabled()
    for (let i = 1; i < PHQ9_ITEM_COUNT; i += 1) {
      await answerItem(user, "phq9", String(i), "Several days")
    }
    expect(screen.getByTestId("intake-continue")).toBeDisabled()

    await answerItem(user, "phq9", String(PHQ9_ITEM_COUNT), "Several days")
    expect(screen.getByTestId("intake-continue")).toBeEnabled()

    await user.click(screen.getByTestId("intake-continue"))
    expect(screen.getByTestId("intake-submit")).toBeDisabled()
    await answerAll(user, "gad7", GAD7_ITEM_COUNT, "Not at all")
    expect(screen.getByTestId("intake-submit")).toBeEnabled()
  })

  it("posts exactly the route's request body, with no patient id in it", async () => {
    const user = await renderReady()
    await advanceToScreener(user, "Panic attacks before work")
    await answerAll(user, "phq9", PHQ9_ITEM_COUNT, "Several days")
    await user.click(screen.getByTestId("intake-continue"))
    await answerAll(user, "gad7", GAD7_ITEM_COUNT, "Not at all")
    await user.click(screen.getByTestId("intake-submit"))

    await waitFor(() => expect(api.submitIntake).toHaveBeenCalledTimes(1))
    const [token, body] = vi.mocked(api.submitIntake).mock.calls[0]
    expect(token).toBe(TOKEN)
    expect(Object.keys(body).sort()).toEqual([
      "corrections",
      "dob_confirmed",
      "gad7",
      "name_confirmed",
      "phq9",
      "reason_text",
    ])
    expect(body).toEqual({
      name_confirmed: true,
      dob_confirmed: true,
      corrections: null,
      reason_text: "Panic attacks before work",
      phq9: Object.fromEntries(
        Array.from({ length: PHQ9_ITEM_COUNT }, (_, i) => [String(i + 1), 1]),
      ),
      gad7: Object.fromEntries(
        Array.from({ length: GAD7_ITEM_COUNT }, (_, i) => [String(i + 1), 0]),
      ),
    })
    expect(JSON.stringify(body)).not.toContain("patient_id")
  })

  it("sends one submission for a double click", async () => {
    const user = await renderReady()
    // Never resolves: the button has to stay shut on its own, not because the
    // screen moved on.
    vi.mocked(api.submitIntake).mockImplementation(() => new Promise(() => {}))
    await advanceToScreener(user)
    await advanceToSubmit(user)

    await user.dblClick(screen.getByTestId("intake-submit"))

    expect(api.submitIntake).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("intake-submit")).toBeDisabled()
  })

  it("shows no total and no severity band on the done screen", async () => {
    const user = await renderReady()
    await advanceToScreener(user)
    await advanceToSubmit(user, "Several days")
    await user.click(screen.getByTestId("intake-submit"))

    const done = await screen.findByTestId("intake-done")
    const text = done.textContent ?? ""
    expect(text).toContain("your responses have been sent to your clinician")
    // The 201 carried 12/"moderate" and 7/"mild".
    expect(text).not.toMatch(/\b12\b/)
    expect(text).not.toMatch(/\b7\b/)
    expect(text).not.toMatch(/minimal|mild|moderate|severe/i)
  })

  it("carries the crisis line on the PHQ-9 step and on the done screen", async () => {
    const user = await renderReady()
    await advanceToScreener(user)

    expect(screen.getByTestId("intake-crisis-footer")).toHaveTextContent(CRISIS_FOOTER)

    // All-zero answers: the line is not a reaction to anything answered.
    await answerAll(user, "phq9", PHQ9_ITEM_COUNT, "Not at all")
    expect(screen.getByTestId("intake-crisis-footer")).toBeInTheDocument()
    await user.click(screen.getByTestId("intake-continue"))
    expect(screen.queryByTestId("intake-crisis-footer")).toBeNull()

    await answerAll(user, "gad7", GAD7_ITEM_COUNT, "Not at all")
    await user.click(screen.getByTestId("intake-submit"))

    await screen.findByTestId("intake-done")
    expect(screen.getByTestId("intake-crisis-footer")).toHaveTextContent(CRISIS_FOOTER)
  })

  it("submits in full after 'Something's not right' and a correction", async () => {
    const user = await renderReady()
    await user.click(screen.getByTestId("intake-identity-deny"))
    await user.type(screen.getByTestId("intake-corrections"), "My last name is spelled Okonkwo-Hale")
    await user.click(screen.getByTestId("intake-continue"))
    await user.type(screen.getByTestId("intake-reason"), "Referred by my GP")
    await user.click(screen.getByTestId("intake-continue"))
    await advanceToSubmit(user)
    await user.click(screen.getByTestId("intake-submit"))

    await screen.findByTestId("intake-done")
    const [, body] = vi.mocked(api.submitIntake).mock.calls[0]
    expect(body.name_confirmed).toBe(false)
    expect(body.dob_confirmed).toBe(false)
    expect(body.corrections).toBe("My last name is spelled Okonkwo-Hale")
  })

  /** Put the real client back and answer it with `status`, end to end. */
  async function renderAgainst(status: number, code: string) {
    const actual =
      await vi.importActual<typeof import("@/lib/api/patientIntake")>("@/lib/api/patientIntake")
    vi.mocked(api.fetchIntakeForm).mockImplementation(actual.fetchIntakeForm)
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(status, { error: { code } })))
    render(<PortalIntakeFlow sessionToken={TOKEN} />)
  }

  it("renders the expired-link guidance for a 401", async () => {
    await renderAgainst(401, "UNAUTHORIZED")

    expect(await screen.findByTestId("intake-expired")).toHaveTextContent("This link has expired")
  })

  it("renders the expired-link guidance for a 403 STEP_UP_REQUIRED", async () => {
    await renderAgainst(403, "STEP_UP_REQUIRED")

    expect(await screen.findByTestId("intake-expired")).toHaveTextContent("This link has expired")
  })

  it("offers a retry when the form cannot be loaded", async () => {
    const user = userEvent.setup()
    vi.mocked(api.fetchIntakeForm)
      .mockRejectedValueOnce(new api.PatientIntakeError("unavailable", "down"))
      .mockResolvedValueOnce(INTAKE_FORM)
    render(<PortalIntakeFlow sessionToken={TOKEN} />)

    await user.click(await screen.findByRole("button", { name: "Try again" }))
    await screen.findByTestId("intake-flow")
  })

  it("keeps the answers when a submission fails, and lets it be sent again", async () => {
    const user = await renderReady()
    vi.mocked(api.submitIntake)
      .mockRejectedValueOnce(new api.PatientIntakeError("rate_limited", "slow down"))
      .mockResolvedValueOnce(INTAKE_SUBMISSION)
    await advanceToScreener(user)
    await advanceToSubmit(user)
    await user.click(screen.getByTestId("intake-submit"))

    expect(await screen.findByTestId("intake-submit-error")).toHaveTextContent(/wait a minute/i)
    await user.click(screen.getByTestId("intake-submit"))
    await screen.findByTestId("intake-done")
    expect(api.submitIntake).toHaveBeenCalledTimes(2)
  })
})
