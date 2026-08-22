// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { EditorialEventCard } from "../EditorialEventCard"
import type { AppointmentResponse } from "@/types/scheduling"

function appointment(overrides: Partial<AppointmentResponse> = {}): AppointmentResponse {
  return {
    id: "a1",
    user_id: "u1",
    patient_id: "p1",
    title: "Some auto title",
    start_at: "2026-06-01T09:00:00",
    end_at: "2026-06-01T09:50:00",
    duration_minutes: 50,
    status: "confirmed",
    session_type: "individual",
    video_link: null,
    video_platform: null,
    notes: null,
    recurrence_rule: null,
    recurring_appointment_id: null,
    recurrence_index: null,
    is_exception: false,
    google_event_id: null,
    google_sync_status: null,
    session_id: null,
    created_at: "2026-06-01T08:00:00",
    updated_at: null,
    ...overrides,
  }
}

describe("EditorialEventCard", () => {
  it("renders the full patient name (not truncated) with 2-line clamp on normal blocks", () => {
    render(
      <EditorialEventCard
        appointment={appointment()}
        patientName="Alexandria Featherstonehaugh-Worthington"
        onClick={vi.fn()}
      />,
    )
    const name = screen.getByText("Alexandria Featherstonehaugh-Worthington")
    expect(name).toBeInTheDocument()
    // Normal/tall blocks wrap (word-break) rather than nowrap-ellipsis like
    // micro. jsdom silently drops the -webkit-box clamp props (unrecognized
    // values), so we assert the wrap behavior it does preserve.
    expect(name).toHaveStyle({ overflow: "hidden", wordBreak: "break-word" })
    expect(name).not.toHaveStyle({ whiteSpace: "nowrap" })
  })

  it("shows the time subline on normal blocks", () => {
    render(
      <EditorialEventCard
        appointment={appointment()}
        patientName="Jane Doe"
        onClick={vi.fn()}
      />,
    )
    expect(screen.getByText(/9:00\s+–\s+9:50\s*AM/)).toBeInTheDocument()
  })

  it("clamps to a single nowrap ellipsis line on micro blocks and hides the time subline", () => {
    render(
      <EditorialEventCard
        appointment={appointment()}
        patientName="Jane Doe"
        onClick={vi.fn()}
        micro
      />,
    )
    const name = screen.getByText("Jane Doe")
    expect(name).toHaveStyle({
      whiteSpace: "nowrap",
      textOverflow: "ellipsis",
      overflow: "hidden",
    })
    expect(screen.queryByText(/9:00\s+–\s+9:50\s*AM/)).not.toBeInTheDocument()
  })

  it("hides the time subline on compact blocks", () => {
    render(
      <EditorialEventCard
        appointment={appointment()}
        patientName="Jane Doe"
        onClick={vi.fn()}
        compact
      />,
    )
    expect(screen.queryByText(/9:00\s+–\s+9:50\s*AM/)).not.toBeInTheDocument()
  })

  it("shows the telehealth video icon only when video_link is present and not compact/micro", () => {
    const { rerender } = render(
      <EditorialEventCard
        appointment={appointment({ video_link: "https://meet.example/abc" })}
        patientName="Jane Doe"
        onClick={vi.fn()}
      />,
    )
    expect(document.querySelector("svg.lucide-video")).toBeInTheDocument()

    rerender(
      <EditorialEventCard
        appointment={appointment({ video_link: "https://meet.example/abc" })}
        patientName="Jane Doe"
        onClick={vi.fn()}
        compact
      />,
    )
    expect(document.querySelector("svg.lucide-video")).not.toBeInTheDocument()
  })

  it("shows a repeating-series icon only when the appointment belongs to a series", () => {
    const { rerender } = render(
      <EditorialEventCard
        appointment={appointment({ recurring_appointment_id: "series-1" })}
        patientName="Jane Doe"
        onClick={vi.fn()}
      />,
    )
    expect(document.querySelector("svg.lucide-repeat")).toBeInTheDocument()

    rerender(
      <EditorialEventCard appointment={appointment()} patientName="Jane Doe" onClick={vi.fn()} />,
    )
    expect(document.querySelector("svg.lucide-repeat")).not.toBeInTheDocument()
  })

  it("does not render a leading person/group or trailing status icon", () => {
    render(
      <EditorialEventCard
        appointment={appointment({ session_type: "group", status: "completed" })}
        patientName="Jane Doe"
        onClick={vi.fn()}
      />,
    )
    expect(document.querySelector("svg.lucide-user")).not.toBeInTheDocument()
    expect(document.querySelector("svg.lucide-users")).not.toBeInTheDocument()
    expect(document.querySelector("svg.lucide-check")).not.toBeInTheDocument()
    expect(document.querySelector("svg.lucide-check-check")).not.toBeInTheDocument()
  })

  it("invokes onClick with the appointment", () => {
    const onClick = vi.fn()
    const appt = appointment()
    render(
      <EditorialEventCard appointment={appt} patientName="Jane Doe" onClick={onClick} />,
    )
    fireEvent.click(screen.getByRole("button"))
    expect(onClick).toHaveBeenCalledWith(appt)
  })
})
