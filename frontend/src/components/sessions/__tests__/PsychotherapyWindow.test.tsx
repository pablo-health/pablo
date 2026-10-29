// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PsychotherapyWindow: the clinician confirms the therapy minutes on a
 * timeline of labeled turns, keeps the minutes they said, or types them, and
 * sees the add-on band and the medical visit's share as facts.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { PsychotherapyWindow } from "../PsychotherapyWindow"
import { peopleWords } from "@/lib/peopleTerm"
import type { PsychotherapyWindow as Window, RecordingTurn, TurnLabel } from "@/types/visitTimes"

const mutate = vi.fn()

vi.mock("@/hooks/usePeopleTerm", () => ({ usePeopleTerm: () => peopleWords("clients") }))
vi.mock("@/hooks/useVisitTimes", () => ({
  useConfirmPsychotherapyWindow: () => ({ mutate, isPending: false, isError: false }),
}))

const TZ = "America/New_York"
// 10:00 AM in New York.
const STARTED = "2026-10-06T14:00:00Z"
// The client left at 50:00.
const END = 50 * 60

function turn(minute: number, until: number, label: TurnLabel | null, text = "…"): RecordingTurn {
  return { seconds: minute * 60, end_seconds: until * 60, speaker: "Therapist", text, label }
}

// Therapy 1-15 and 20-40 (34 minutes), medication 15-20, a risk screen 40-45.
const TURNS = [
  turn(0, 1, "admin"),
  turn(1, 15, "therapy"),
  turn(15, 20, "medication_management"),
  turn(20, 30, "therapy"),
  turn(30, 40, "therapy"),
  turn(40, 45, "screening_risk"),
  turn(45, 50, "admin"),
]

function window(overrides: Partial<Window> = {}): Window {
  return {
    offered: true,
    end_seconds: END,
    turns: TURNS,
    runs: [],
    labeled_minutes: 34,
    cue_seconds: 60,
    dictated: null,
    confirmed_start_seconds: null,
    confirmed_minutes: null,
    contiguous: null,
    labels_confirmed: false,
    window_text: null,
    dictated_time: null,
    disagrees: false,
    ...overrides,
  }
}

function renderWindow(w: Window, { readonly = false } = {}) {
  return render(
    <PsychotherapyWindow
      sessionId="s1"
      window={w}
      startedAt={STARTED}
      timeZone={TZ}
      readonly={readonly}
    />,
  )
}

const runs = () => screen.getAllByTestId("timeline-run").map((r) => r.getAttribute("data-label"))

describe("PsychotherapyWindow", () => {
  beforeEach(() => vi.clearAllMocks())

  it("shows the labeled turns as a timeline with the therapy total", () => {
    renderWindow(window())

    expect(runs()).toEqual([
      "admin",
      "therapy",
      "medication_management",
      "therapy",
      "screening_risk",
      "admin",
    ])
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent(
      "34 therapy minutes of 50 · 16–37 minutes",
    )
    expect(screen.getByTestId("em-remainder")).toHaveTextContent("Medical visit: 16 min")
  })

  it("relabels a run, changing the total and the band, and confirms the labels", async () => {
    renderWindow(window())

    await userEvent.click(screen.getByRole("button", { name: "Medication, 10:15 AM to 10:20 AM" }))
    await userEvent.click(
      within(screen.getByRole("group", { name: "Label 10:15 AM to 10:20 AM" })).getByRole("button", {
        name: "Therapy",
      }),
    )

    expect(runs()).toEqual(["admin", "therapy", "screening_risk", "admin"])
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent(
      "39 therapy minutes of 50 · 38–52 minutes",
    )
    await userEvent.click(screen.getByRole("button", { name: "Confirm" }))
    expect(mutate).toHaveBeenCalledWith(
      {
        labels: [
          { seconds: 0, label: "admin" },
          { seconds: 60, label: "therapy" },
          { seconds: 900, label: "therapy" },
          { seconds: 1200, label: "therapy" },
          { seconds: 1800, label: "therapy" },
          { seconds: 2400, label: "screening_risk" },
          { seconds: 2700, label: "admin" },
        ],
        time_zone: TZ,
      },
      expect.anything(),
    )
  })

  it("moves a boundary one turn with the arrow keys", () => {
    renderWindow(window())

    // The boundary between the medication check and the therapy at 10:20.
    const boundary = screen.getByRole("slider", { name: "Boundary at 10:20 AM" })
    fireEvent.keyDown(boundary, { key: "ArrowRight" })

    // The 20-30 turn joins the medication check: 10 fewer therapy minutes.
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent("24 therapy minutes")
    expect(screen.getByRole("slider", { name: "Boundary at 10:30 AM" })).toBeInTheDocument()
  })

  it("flags under five minutes left for the medical visit, without blocking", () => {
    const allTherapy = TURNS.map((t) => ({ ...t, label: "therapy" as const }))
    renderWindow(window({ turns: allTherapy }))

    expect(screen.getByTestId("em-remainder")).toHaveTextContent("Medical visit: 0 min")
    expect(screen.getByTestId("em-remainder-flag")).toHaveTextContent(
      "Under 5 minutes left for the medical visit.",
    )
    expect(screen.getByRole("button", { name: "Confirm" })).toBeEnabled()
  })

  it("shows the medical visit's minutes without a flag from five minutes up", () => {
    renderWindow(window())

    expect(screen.getByTestId("em-remainder")).toHaveTextContent("Medical visit: 16 min")
    expect(screen.queryByTestId("em-remainder-flag")).not.toBeInTheDocument()
  })

  it("keeps the minutes the clinician said", async () => {
    renderWindow(window({ dictated: { start: null, end: null, minutes: 22, as_dictated: "22 minutes" } }))

    expect(screen.getByText("You said 22 minutes.")).toBeInTheDocument()
    await userEvent.click(screen.getByRole("button", { name: "Use my minutes" }))
    expect(mutate).toHaveBeenCalledWith({ minutes: 22, time_zone: TZ }, expect.anything())
  })

  it("takes typed minutes, and refuses more than the client was present", async () => {
    renderWindow(window())

    await userEvent.click(screen.getByRole("button", { name: "Type the minutes" }))
    await userEvent.type(screen.getByLabelText("Psychotherapy minutes"), "15")
    expect(screen.getByTestId("psychotherapy-preview")).toHaveTextContent(
      "15 therapy minutes of 50 · Under the add-on minimum",
    )

    await userEvent.clear(screen.getByLabelText("Psychotherapy minutes"))
    await userEvent.type(screen.getByLabelText("Psychotherapy minutes"), "51")
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Can't be more than the 50 minutes the client was present.",
    )
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDisabled()
  })

  it("shows the confirmed time with its band", () => {
    renderWindow(
      window({
        confirmed_minutes: 34,
        contiguous: false,
        labels_confirmed: true,
        window_text: "34 minutes (interleaved with medication management; time accounted separately)",
      }),
    )

    expect(screen.getByTestId("psychotherapy-confirmed")).toHaveTextContent(
      "34 minutes (interleaved with medication management; time accounted separately)",
    )
    expect(screen.getByTestId("add-on-band")).toHaveTextContent("16–37 minutes")
    expect(screen.queryByTestId("therapy-timeline")).not.toBeInTheDocument()
  })

  it("flags a dictated time that disagrees and re-sends the confirmed labels with the pick", async () => {
    renderWindow(
      window({
        confirmed_minutes: 34,
        labels_confirmed: true,
        window_text: "34 minutes",
        dictated_time: "22 minutes",
        disagrees: true,
      }),
    )

    expect(screen.getByRole("alert")).toHaveTextContent(
      "You said “22 minutes”. The transcript shows 34 minutes.",
    )
    await userEvent.click(screen.getByRole("button", { name: "Keep what you said" }))
    expect(mutate).toHaveBeenCalledWith(
      expect.objectContaining({ resolution: "keep_dictated", labels: expect.any(Array) }),
      expect.anything(),
    )
    await userEvent.click(screen.getByRole("button", { name: "Use 34 minutes" }))
    expect(mutate).toHaveBeenLastCalledWith(
      expect.objectContaining({ resolution: "use_confirmed", time_zone: TZ }),
      expect.anything(),
    )
  })

  it("only shows the time on a signed note", () => {
    renderWindow(window({ confirmed_minutes: 34, window_text: "34 minutes" }), { readonly: true })

    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })
})
