// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * PortalRefills: the whole surface over a mocked client — the requests
 * first with the form behind a button, ask for a refill and see it land
 * at the top of the list with a confirmation, and fail plainly.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import type { RefillRequest } from "@/lib/api/patientRefills"
import * as api from "@/lib/api/patientRefills"
import * as shellApi from "@/lib/portal-shell/api"
import { PortalRefills } from "../PortalRefills"

vi.mock("@/lib/api/patientRefills", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientRefills")>()
  return {
    ...actual,
    listRefillMedications: vi.fn(),
    listRefillRequests: vi.fn(),
    createRefillRequest: vi.fn(),
  }
})

vi.mock("@/lib/portal-shell/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/portal-shell/api")>()
  return { ...actual, fetchCapabilities: vi.fn() }
})

const TOKEN = "session-token"

const older: RefillRequest = {
  id: "r-old",
  medication_id: "med-1",
  medication_text: "Sertraline 50 mg",
  pharmacy_text: null,
  patient_note: null,
  status: "approved",
  created_at: "2026-08-20T15:00:00Z",
  decided_at: "2026-08-21T15:00:00Z",
}

const created: RefillRequest = {
  id: "r-new",
  medication_id: null,
  medication_text: "Lamotrigine",
  pharmacy_text: null,
  patient_note: null,
  status: "requested",
  created_at: "2026-09-03T15:00:00Z",
  decided_at: null,
}

function renderSurface() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "PortalRefillsWrapper"
  return render(<PortalRefills sessionToken={TOKEN} />, { wrapper: Wrapper })
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.listRefillMedications).mockResolvedValue({
    data: [{ id: "med-1", drug_name: "Sertraline", dose: "50 mg" }],
    total: 1,
  })
  vi.mocked(api.listRefillRequests).mockResolvedValue({ data: [older], total: 1 })
  vi.mocked(api.createRefillRequest).mockResolvedValue(created)
  vi.mocked(shellApi.fetchCapabilities).mockResolvedValue({
    ok: true,
    data: {
      practice: { display_name: "Maple Street Therapy" },
      modules: { refills: true, messaging: true },
      auth_strength: "session",
    },
  })
})

describe("PortalRefills", () => {
  it("shows the requests first, with the form behind a button", async () => {
    const user = userEvent.setup()
    renderSurface()

    await screen.findByTestId("portal-refills-request-r-old")
    expect(screen.queryByTestId("portal-refills-form")).toBeNull()

    await user.click(screen.getByTestId("portal-refills-open-form"))
    expect(screen.getByTestId("portal-refills-form")).toBeTruthy()

    await user.click(screen.getByTestId("portal-refills-cancel"))
    expect(screen.queryByTestId("portal-refills-form")).toBeNull()
  })

  it("starts with the form open when there are no requests yet", async () => {
    vi.mocked(api.listRefillRequests).mockResolvedValue({ data: [], total: 0 })

    renderSurface()

    expect(await screen.findByTestId("portal-refills-form")).toBeTruthy()
    expect(screen.queryByTestId("portal-refills-open-form")).toBeNull()
    expect(screen.queryByTestId("portal-refills-cancel")).toBeNull()
  })

  it("sends a request, closes the form, confirms, and marks the new row", async () => {
    vi.mocked(api.listRefillRequests)
      .mockResolvedValueOnce({ data: [older], total: 1 })
      .mockResolvedValue({ data: [created, older], total: 2 })
    const user = userEvent.setup()
    renderSurface()

    await user.click(await screen.findByTestId("portal-refills-open-form"))
    await user.click(screen.getByLabelText("Something else"))
    await user.type(screen.getByLabelText("Medication name"), "Lamotrigine")
    await user.click(screen.getByTestId("portal-refills-submit"))

    expect(api.createRefillRequest).toHaveBeenCalledWith(TOKEN, {
      medication_id: null,
      medication_text: "Lamotrigine",
      pharmacy_text: null,
      patient_note: null,
    })

    await screen.findByTestId("portal-refills-request-r-new")
    const ids = screen
      .getAllByRole("listitem")
      .map((item) => item.getAttribute("data-testid"))
    expect(ids).toEqual(["portal-refills-request-r-new", "portal-refills-request-r-old"])
    expect(screen.getByTestId("portal-refills-status-r-new").textContent).toBe("Received")
    expect(
      screen.getByTestId("portal-refills-request-r-new").getAttribute("data-highlighted"),
    ).toBe("true")
    expect(
      screen.getByTestId("portal-refills-request-r-old").getAttribute("data-highlighted"),
    ).toBeNull()
    expect(screen.getByTestId("portal-refills-sent").textContent).toBe(
      "Sent to Maple Street Therapy. You'll see the answer here.",
    )
    expect(screen.queryByTestId("portal-refills-form")).toBeNull()
    expect(screen.getByTestId("portal-refills-open-form")).toBeTruthy()
  })

  it("says your practice when the practice's name is not available", async () => {
    vi.mocked(shellApi.fetchCapabilities).mockResolvedValue({ ok: false })
    const user = userEvent.setup()
    renderSurface()

    await user.click(await screen.findByTestId("portal-refills-open-form"))
    await user.click(screen.getByLabelText("Sertraline 50 mg"))
    await user.click(screen.getByTestId("portal-refills-submit"))

    expect((await screen.findByTestId("portal-refills-sent")).textContent).toBe(
      "Sent to your practice. You'll see the answer here.",
    )
  })

  it("points a declined request at messaging when messaging is on", async () => {
    const declined: RefillRequest = { ...older, id: "r-declined", status: "declined" }
    vi.mocked(api.listRefillRequests).mockResolvedValue({ data: [declined], total: 1 })

    renderSurface()

    expect((await screen.findByTestId("portal-refills-next-r-declined")).textContent).toBe(
      "Message your practice if you have questions.",
    )
  })

  it("leaves the messaging line off a declined request when messaging is off", async () => {
    const declined: RefillRequest = { ...older, id: "r-declined", status: "declined" }
    vi.mocked(api.listRefillRequests).mockResolvedValue({ data: [declined], total: 1 })
    vi.mocked(shellApi.fetchCapabilities).mockResolvedValue({
      ok: true,
      data: {
        practice: { display_name: "Maple Street Therapy" },
        modules: { refills: true, messaging: false },
        auth_strength: "session",
      },
    })

    renderSurface()

    await screen.findByTestId("portal-refills-request-r-declined")
    await waitFor(() => expect(shellApi.fetchCapabilities).toHaveBeenCalled())
    expect(screen.queryByTestId("portal-refills-next-r-declined")).toBeNull()
  })

  it("shows a short error and keeps the choice when the send fails", async () => {
    vi.mocked(api.createRefillRequest).mockRejectedValue(new api.PatientRefillsError(500))
    const user = userEvent.setup()
    renderSurface()

    await user.click(await screen.findByTestId("portal-refills-open-form"))
    await user.click(screen.getByLabelText("Sertraline 50 mg"))
    await user.click(screen.getByTestId("portal-refills-submit"))

    expect((await screen.findByTestId("portal-refills-error")).textContent).toBe(
      "That didn't send. Try again.",
    )
    expect((screen.getByLabelText("Sertraline 50 mg") as HTMLInputElement).checked).toBe(true)
  })

  it("still offers the name field when the medication list cannot be loaded", async () => {
    vi.mocked(api.listRefillMedications).mockRejectedValue(new api.PatientRefillsError(500))
    const user = userEvent.setup()
    renderSurface()

    await user.click(await screen.findByTestId("portal-refills-open-form"))
    expect(screen.getByLabelText("Medication name")).toBeTruthy()
    expect(screen.queryAllByRole("radio")).toHaveLength(0)
  })

  it("says so plainly when the requests cannot be loaded", async () => {
    vi.mocked(api.listRefillRequests).mockRejectedValue(new api.PatientRefillsError(500))

    renderSurface()

    await waitFor(() =>
      expect(screen.getByTestId("portal-refills-load-error").textContent).toContain(
        "Refills aren't loading",
      ),
    )
    // Asking still works, so the form is there without a button press.
    expect(screen.getByTestId("portal-refills-form")).toBeTruthy()
  })
})
