// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { renderWithProviders } from "@/test/renderWithProviders"
import type { AiConsentRecord } from "@/types/aiConsent"
import { StartSessionButton } from "../StartSessionButton"

function renderButton(appointmentId = "appt-1") {
  return renderWithProviders(
    <StartSessionButton appointmentId={appointmentId} patientId="patient-1" />,
  )
}

const fetchAiNotesConsentSetting = vi.hoisted(() => vi.fn())
const fetchAiConsent = vi.hoisted(() => vi.fn())
const recordAiConsent = vi.hoisted(() => vi.fn())

vi.mock("@/lib/api/aiConsent", () => ({
  fetchAiNotesConsentSetting: (...args: unknown[]) => fetchAiNotesConsentSetting(...args),
  fetchAiConsent: (...args: unknown[]) => fetchAiConsent(...args),
  recordAiConsent: (...args: unknown[]) => recordAiConsent(...args),
}))

function practiceAsks(asks: boolean) {
  fetchAiNotesConsentSetting.mockResolvedValue({
    ask_clients_about_ai_notes: asks,
    audio_retention_days: 365,
    can_change: true,
  })
}

function answerOnFile(decision: "consented" | "declined" | null): AiConsentRecord {
  const current =
    decision === null
      ? null
      : {
          id: "entry-1",
          decision,
          effective_on: "2026-09-14",
          source: "clinician" as const,
          recorded_by_name: "Sam Lee",
          recorded_at: "2026-09-14T15:00:00Z",
        }
  const record = { current, history: current ? [current] : [] }
  fetchAiConsent.mockResolvedValue(record)
  return record
}

beforeEach(() => {
  practiceAsks(false)
  answerOnFile(null)
})

const createLaunchIntent = vi.hoisted(() => vi.fn())
const clickThroughAnchor = vi.hoisted(() => vi.fn())
const armNoHandoffFallback = vi.hoisted(() =>
  vi.fn((_onNoHandoff: () => void): (() => void) => () => {}),
)

vi.mock("@/lib/api/devices", () => ({
  createLaunchIntent: (...args: unknown[]) => createLaunchIntent(...args),
}))

vi.mock("@/lib/companionLaunch", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/companionLaunch")>()
  return {
    ...actual,
    clickThroughAnchor: (...args: unknown[]) => clickThroughAnchor(...args),
    armNoHandoffFallback: (onNoHandoff: () => void) =>
      armNoHandoffFallback(onNoHandoff),
  }
})

afterEach(() => {
  vi.clearAllMocks()
})

describe("StartSessionButton", () => {
  it("prefetches the launch intent on hover and exposes it as the anchor href", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-abc",
      launch_url: "https://app.pablo.health/launch/intent-abc",
      expires_in: 180,
    })
    const user = userEvent.setup()

    renderButton("appt-1")
    const link = screen.getByRole("link", { name: /start session/i })
    // Inert until prefetched — no Universal Link href yet.
    expect(link).toHaveAttribute("href", "#")

    await user.hover(link)

    expect(createLaunchIntent).toHaveBeenCalledWith("appt-1")
    await waitFor(() =>
      expect(link).toHaveAttribute(
        "href",
        "https://app.pablo.health/launch/intent-abc",
      ),
    )
  })

  it("arms the no-handoff fallback on a real anchor click without re-POSTing", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-abc",
      launch_url: "https://app.pablo.health/launch/intent-abc",
      expires_in: 180,
    })
    const user = userEvent.setup()

    renderButton("appt-1")
    const link = screen.getByRole("link", { name: /start session/i })

    // Hover prefetches; the click then drives the real (verified-link) anchor.
    await user.hover(link)
    await waitFor(() => expect(link).not.toHaveAttribute("href", "#"))
    await user.click(link)

    // The verified link is the anchor's own default navigation — we do NOT
    // synthesize a click for it; we only arm the legacy fallback timer.
    expect(armNoHandoffFallback).toHaveBeenCalledTimes(1)
    // Exactly one intent issued — the prefetched one is reused.
    expect(createLaunchIntent).toHaveBeenCalledTimes(1)
  })

  it("falls back to the legacy scheme with the SAME intent when no handoff happens", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-xyz",
      launch_url: "https://dev.pablo.health/launch/intent-xyz",
      expires_in: 180,
    })
    const user = userEvent.setup()

    renderButton("appt-2")
    const link = screen.getByRole("link", { name: /start session/i })

    await user.hover(link)
    await waitFor(() => expect(link).not.toHaveAttribute("href", "#"))
    await user.click(link)

    // Simulate the no-handoff timer elapsing by invoking the callback the
    // component handed to armNoHandoffFallback.
    const onNoHandoff = armNoHandoffFallback.mock.calls[0][0] as () => void
    onNoHandoff()

    expect(clickThroughAnchor).toHaveBeenLastCalledWith(
      "pablohealth://session/start?intent=intent-xyz",
    )
    // Exactly one intent issued — the fallback reuses it, never re-POSTs.
    expect(createLaunchIntent).toHaveBeenCalledTimes(1)
  })

  it("does not orphan the fallback timer on a rapid second click", async () => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-xyz",
      launch_url: "https://dev.pablo.health/launch/intent-xyz",
      expires_in: 180,
    })
    const cleanup = vi.fn()
    armNoHandoffFallback.mockReturnValue(cleanup)
    const user = userEvent.setup()

    renderButton("appt-2")
    const link = screen.getByRole("link", { name: /start session/i })

    await user.hover(link)
    await waitFor(() => expect(link).not.toHaveAttribute("href", "#"))
    await user.click(link)
    // A second click while the no-handoff window is still open is a no-op:
    // no new fallback armed, no new intent issued.
    await user.click(link)

    expect(armNoHandoffFallback).toHaveBeenCalledTimes(1)
    expect(createLaunchIntent).toHaveBeenCalledTimes(1)
    expect(cleanup).not.toHaveBeenCalled()
  })

  it("a click while the hover's intent is still in flight waits for it and hands off", async () => {
    let issue: (value: unknown) => void = () => {}
    createLaunchIntent.mockReturnValue(new Promise((resolve) => (issue = resolve)))
    const user = userEvent.setup()

    renderButton("appt-1")
    const link = screen.getByRole("link", { name: /start session/i })
    await user.hover(link)
    await user.click(link)
    issue({
      intent_id: "intent-late",
      launch_url: "https://app.pablo.health/launch/intent-late",
      expires_in: 180,
    })

    await waitFor(() =>
      expect(clickThroughAnchor).toHaveBeenCalledWith(
        "https://app.pablo.health/launch/intent-late",
      ),
    )
    expect(createLaunchIntent).toHaveBeenCalledTimes(1)
  })

  it("does nothing destructive when intent issuance fails on click", async () => {
    createLaunchIntent.mockRejectedValue(new Error("flag off"))
    const user = userEvent.setup()

    renderButton("appt-3")
    const link = screen.getByRole("link", { name: /start session/i })

    // No hover prefetch — fetch-on-click path, which rejects.
    await user.click(link)

    expect(clickThroughAnchor).not.toHaveBeenCalled()
    expect(armNoHandoffFallback).not.toHaveBeenCalled()
    // Anchor stays inert (still '#') and re-armable for a retry.
    expect(link).toHaveAttribute("href", "#")
    expect(link).not.toHaveAttribute("aria-disabled", "true")
  })
})

describe("StartSessionButton and the client's answer about AI-assisted notes", () => {
  const LAUNCH_URL = "https://app.pablo.health/launch/intent-abc"

  beforeEach(() => {
    createLaunchIntent.mockResolvedValue({
      intent_id: "intent-abc",
      launch_url: LAUNCH_URL,
      expires_in: 180,
    })
  })

  async function clickStart() {
    const user = userEvent.setup()
    renderButton()
    await user.click(screen.getByRole("link", { name: /start session/i }))
    return user
  }

  it("says a declined client declined, links to the chart, and does not hand off", async () => {
    practiceAsks(true)
    answerOnFile("declined")

    await clickStart()

    const dialog = await screen.findByRole("dialog", { name: "AI-assisted notes declined" })
    expect(dialog).toHaveTextContent(
      "This client declined AI-assisted notes on Sep 14, 2026.",
    )
    expect(within(dialog).getByRole("link", { name: "Open chart" })).toHaveAttribute(
      "href",
      "/dashboard/patients/patient-1",
    )
    expect(within(dialog).queryByRole("button", { name: "Record anyway" })).toBeNull()
    expect(clickThroughAnchor).not.toHaveBeenCalled()
    expect(armNoHandoffFallback).not.toHaveBeenCalled()
  })

  it("asks when nobody has, and 'Client agreed today' records it and then hands off", async () => {
    practiceAsks(true)
    answerOnFile(null)
    recordAiConsent.mockResolvedValue({ current: null, history: [] })

    const user = await clickStart()
    const dialog = await screen.findByRole("dialog", { name: "No consent on file" })
    expect(clickThroughAnchor).not.toHaveBeenCalled()

    await user.click(within(dialog).getByRole("button", { name: "Client agreed today" }))

    await waitFor(() => expect(clickThroughAnchor).toHaveBeenCalledWith(LAUNCH_URL))
    expect(recordAiConsent).toHaveBeenCalledTimes(1)
    // No date: the server records the clinician's own today.
    expect(recordAiConsent).toHaveBeenCalledWith("patient-1", { decision: "consented" }, undefined)
    expect(armNoHandoffFallback).toHaveBeenCalledTimes(1)
  })

  it("'Record anyway' hands off without writing anything, and does not ask again", async () => {
    practiceAsks(true)
    answerOnFile(null)

    const user = await clickStart()
    const dialog = await screen.findByRole("dialog", { name: "No consent on file" })
    await user.click(within(dialog).getByRole("button", { name: "Record anyway" }))

    expect(clickThroughAnchor).toHaveBeenCalledWith(LAUNCH_URL)
    expect(recordAiConsent).not.toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("'Cancel' closes without handing off or writing", async () => {
    practiceAsks(true)
    answerOnFile(null)

    const user = await clickStart()
    const dialog = await screen.findByRole("dialog", { name: "No consent on file" })
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }))

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
    expect(clickThroughAnchor).not.toHaveBeenCalled()
    expect(recordAiConsent).not.toHaveBeenCalled()
  })

  it("hands off with no prompt when the practice does not ask, even for a decline", async () => {
    practiceAsks(false)
    answerOnFile("declined")

    await clickStart()

    // Handed off by the anchor's own navigation, with the fallback armed.
    await waitFor(() => expect(armNoHandoffFallback).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole("dialog")).toBeNull()
    // The client's answer is not even read.
    expect(fetchAiConsent).not.toHaveBeenCalled()
  })

  it("hands off with no prompt for a client who agreed", async () => {
    practiceAsks(true)
    answerOnFile("consented")

    await clickStart()

    await waitFor(() => expect(armNoHandoffFallback).toHaveBeenCalledTimes(1))
    expect(screen.queryByRole("dialog")).toBeNull()
  })
})
