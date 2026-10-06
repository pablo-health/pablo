// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PsychotherapyWindow: the clinician confirms where the therapy portion
 * started, or types the minutes, and sees the add-on band as a fact.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PsychotherapyWindow } from "../PsychotherapyWindow"
import { peopleWords } from "@/lib/peopleTerm"
import type { PsychotherapyWindow as Window } from "@/types/visitTimes"

const mutate = vi.fn()

vi.mock("@/hooks/usePeopleTerm", () => ({ usePeopleTerm: () => peopleWords("clients") }))
vi.mock("@/hooks/useVisitTimes", () => ({
  useConfirmPsychotherapyWindow: () => ({ mutate, isPending: false, isError: false }),
}))

const TZ = "America/New_York"
// 10:00 AM in New York.
const STARTED = "2026-10-06T14:00:00Z"
// The client left at 65:00.
const END = 65 * 60

function window(overrides: Partial<Window> = {}): Window {
  return {
    offered: true,
    end_seconds: END,
    turns: [
      { seconds: 5, speaker: "Therapist", text: "How has the medication been?" },
      { seconds: 750, speaker: "Therapist", text: "Now let's get into the session work." },
      { seconds: 1800, speaker: "Client", text: "I keep replaying the argument." },
    ],
    candidates: [
      { seconds: 750, source: "marked" },
      { seconds: 1800, source: "attributed" },
    ],
    stated_clock_time: null,
    confirmed_start_seconds: null,
    confirmed_minutes: null,
    window_text: null,
    dictated_time: null,
    disagrees: false,
    ...overrides,
  }
}

function renderWindow(w: Window, readonly = false) {
  return render(
    <PsychotherapyWindow sessionId="s1" window={w} startedAt={STARTED} timeZone={TZ} readonly={readonly} />,
  )
}

describe("PsychotherapyWindow", () => {
  beforeEach(() => vi.clearAllMocks())

  it("offers the proposed starts, previews the minutes, and confirms the chosen start", async () => {
    renderWindow(window())

    expect(screen.getByLabelText(/10:12 AM \(where the draft puts it\)/)).toBeChecked()
    expect(screen.getByLabelText(/10:30 AM \(where the interventions begin\)/)).toBeInTheDocument()
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent("52 minutes · 38–52 minutes")

    await userEvent.click(screen.getByLabelText(/10:30 AM/))
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent("35 minutes · 16–37 minutes")

    await userEvent.click(screen.getByRole("button", { name: "Confirm" }))
    expect(mutate).toHaveBeenCalledWith(
      { start_seconds: 1800, time_zone: TZ },
      expect.anything(),
    )
  })

  it("lets the clinician pick another turn on the transcript", async () => {
    renderWindow(window({ candidates: [] }))

    await userEvent.selectOptions(screen.getByLabelText("Therapy started at"), "5")
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent(
      "64 minutes · 53 minutes or more",
    )
  })

  it("offers a start time the clinician said aloud first", () => {
    renderWindow(window({ stated_clock_time: "around 10:15" }))

    expect(screen.getByLabelText(/10:15 AM \(you said “around 10:15”\)/)).toBeChecked()
  })

  it("takes typed minutes, and refuses more than the client was present", async () => {
    renderWindow(window())

    await userEvent.click(screen.getByLabelText("Type the minutes"))
    await userEvent.type(screen.getByLabelText("Psychotherapy minutes"), "15")
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent(
      "15 minutes · Under the add-on minimum",
    )

    await userEvent.clear(screen.getByLabelText("Psychotherapy minutes"))
    await userEvent.type(screen.getByLabelText("Psychotherapy minutes"), "66")
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Can't be more than the 65 minutes the client was present.",
    )
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled()
  })

  it("shows the confirmed window with its band", () => {
    renderWindow(
      window({
        confirmed_start_seconds: 750,
        confirmed_minutes: 52,
        window_text: "10:12 AM to 11:05 AM, 52 minutes",
      }),
    )

    expect(screen.getByTestId("psychotherapy-confirmed")).toHaveTextContent(
      "10:12 AM to 11:05 AM, 52 minutes",
    )
    expect(screen.getByTestId("add-on-band")).toHaveTextContent("38–52 minutes")
    expect(screen.queryByRole("button", { name: "Confirm" })).not.toBeInTheDocument()
  })

  it("flags a dictated time that disagrees and lets the clinician pick", async () => {
    renderWindow(
      window({
        confirmed_start_seconds: 750,
        confirmed_minutes: 52,
        window_text: "10:12 AM to 11:05 AM, 52 minutes",
        dictated_time: "10:15 to 11:00, 45 minutes",
        disagrees: true,
      }),
    )

    expect(screen.getByRole("alert")).toHaveTextContent(
      "You said “10:15 to 11:00, 45 minutes”. The transcript shows 10:12 AM to 11:05 AM, 52 minutes.",
    )
    await userEvent.click(screen.getByRole("button", { name: "Keep what you said" }))
    expect(mutate).toHaveBeenCalledWith(
      { start_seconds: 750, time_zone: TZ, resolution: "keep_dictated" },
      expect.anything(),
    )
    await userEvent.click(screen.getByRole("button", { name: "Use 52 minutes" }))
    expect(mutate).toHaveBeenLastCalledWith(
      { start_seconds: 750, time_zone: TZ, resolution: "use_confirmed" },
      expect.anything(),
    )
  })

  it("only shows the window on a signed note", () => {
    renderWindow(
      window({ confirmed_start_seconds: 750, confirmed_minutes: 52, window_text: "52 minutes" }),
      true,
    )

    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
})
