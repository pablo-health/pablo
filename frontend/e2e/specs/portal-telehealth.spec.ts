// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A patient joins a video appointment from the portal, in a browser.
 *
 * The unit tests prove the window arithmetic over a mocked client. This
 * proves the part only a real stack can: that the practice's window reaches
 * the portal at all, that the link the practice put on the appointment is the
 * one the button opens, and — for a waiting room — that the URL the patient
 * is sent carries the check-in parameters and an opaque handle rather than
 * anything that identifies them.
 *
 * Only the two providers that need no vendor account are exercised here. Zoom
 * and Google Meet produce a room by calling somebody else's service, so a
 * spec covering them would be testing that service's uptime; they are proved
 * by unit and contract tests against captured responses instead.
 */

import { expect, test } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import {
  givePortalContactDetails,
  givePortalInvitation,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient, type Patient } from "../fixtures/scenarios"

const ROOM_URL = "https://testclinic.example.test/e2e-room"

/** A patient of this practice who can be invited into the portal. */
async function givePortalPatient(api: ApiClient): Promise<{
  patient: Patient
  email: string
  phone: string
}> {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone, date_of_birth: "1988-04-02" })
  return { patient, email, phone }
}

interface ClinicianAppointment {
  id: string
  video_link: string | null
  provider: string | null
  meeting_external_id: string | null
}

/**
 * An appointment `minutesFromNow` out, as the practice would make it.
 *
 * Minutes rather than days because the join window is the thing under test:
 * one a few minutes out is inside it and one next week is not, and no clock
 * seam is needed to tell them apart.
 */
async function giveAppointment(
  api: ApiClient,
  patientId: string,
  minutesFromNow: number,
  extra: Record<string, unknown> = {},
): Promise<ClinicianAppointment> {
  const start = new Date(Date.now() + minutesFromNow * 60_000)
  return api.post<ClinicianAppointment>("/api/appointments", {
    patient_id: patientId,
    title: "Session",
    start_at: start.toISOString(),
    end_at: new Date(start.getTime() + 50 * 60_000).toISOString(),
    duration_minutes: 50,
    session_type: "individual",
    ...extra,
  })
}

test.describe("portal telehealth", () => {
  test("a patient is given the link when the session is about to start, and told to wait before that", async ({
    api,
    page,
  }) => {
    const { patient, email, phone } = await givePortalPatient(api)

    // One starting in five minutes, and one next week. The practice's window
    // is fifteen minutes, so the first is joinable and the second is not.
    const soon = await giveAppointment(api, patient.id, 5, {
      video_link: "https://meet.example.test/e2e-soon",
    })
    await giveAppointment(api, patient.id, 7 * 24 * 60, {
      video_link: "https://meet.example.test/e2e-later",
    })

    // A pasted link is the room, whatever any preference says.
    expect(soon.video_link).toBe("https://meet.example.test/e2e-soon")
    expect(soon.provider).toBe("manual")

    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))

    await expect(page.getByTestId("appointments-row")).toHaveCount(2)

    // The one about to start offers the way in.
    const join = page.getByTestId("appointments-row-join")
    await expect(join).toHaveCount(1)
    await expect(join).toHaveAttribute("href", "https://meet.example.test/e2e-soon")

    // The one next week says when the link turns up instead, which is the
    // assertion that matters: a row with neither would leave a patient
    // expecting to be seen online with nothing to read.
    const later = page.getByTestId("appointments-row-join-later")
    await expect(later).toHaveCount(1)
    await expect(later).toContainText("shortly before your appointment")
  })

  test("a waiting room's link carries the check-in parameters and an opaque handle", async ({
    api,
    page,
  }) => {
    await api.put("/api/telehealth/room-url", { room_url: ROOM_URL })

    const { patient, email, phone } = await givePortalPatient(api)
    const appointment = await giveAppointment(api, patient.id, 5, { provider: "doxy_me" })

    expect(appointment.provider).toBe("doxy_me")
    const url = new URL(appointment.video_link ?? "")
    expect(url.origin + url.pathname).toBe(ROOM_URL)
    expect(url.searchParams.get("autocheckin")).toBe("true")
    expect(url.searchParams.get("username")).toBe(patient.first_name)

    // The handle identifies the visit to us and nothing to anybody else. The
    // URL is mailed to people and lands in the vendor's access log, so this
    // is the assertion the whole `pid` design exists for.
    const handle = url.searchParams.get("pid")
    expect(handle).toBeTruthy()
    expect(handle).not.toBe(appointment.id)
    expect(handle).not.toBe(patient.id)
    expect(appointment.video_link).not.toContain(appointment.id)
    expect(appointment.video_link).not.toContain(patient.id)
    expect(appointment.meeting_external_id).toBe(handle)

    // And the patient's own screen offers exactly that URL.
    await signInToPortal(page, await givePortalInvitation(api, patient.id, email, phone))
    await expect(page.getByTestId("appointments-row-join")).toHaveAttribute(
      "href",
      appointment.video_link ?? "",
    )
  })
})
