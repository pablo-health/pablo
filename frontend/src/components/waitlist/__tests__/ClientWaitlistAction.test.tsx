// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The client page's waitlist action: "Add to waitlist" opens a dialog whose
 * session type starts at the client's usual one and whose format is filled
 * in only from a single past format; nothing is sent until a day, a time of
 * day and a format are chosen; a refusal shows the server's reason. A client
 * already on the list shows their place and can be taken off. A client who
 * is not active cannot be added, and the button says why.
 *
 * A client with usual times (an active scheduling.availability) starts the
 * dialog from them. An entry saved with other times offers one tap to save
 * them as the client's usual times: a new soft row, or the existing row
 * updated. Closing instead writes nothing to the client's record.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react"
import { ApiError } from "@/lib/api/client"
import type { Preference } from "@/types/patientPreferences"
import type { PatientWaitlist, WaitlistEntry } from "@/types/waitlist"
import { ClientWaitlistAction } from "../ClientWaitlistAction"

let patientWaitlist: PatientWaitlist | undefined
let preferences: Preference[] | undefined
const add = vi.fn()
const remove = vi.fn()
const showToast = vi.fn()
const addPreference = vi.fn()
const updatePreference = vi.fn()

vi.mock("@/hooks/usePatientPreferences", () => ({
  usePatientPreferences: (patientId: string | undefined) => ({
    data: patientId && preferences ? { data: preferences, total: preferences.length } : undefined,
  }),
  useAddPreference: () => ({ mutateAsync: addPreference, isPending: false }),
  useUpdatePreference: () => ({ mutateAsync: updatePreference, isPending: false }),
}))

vi.mock("@/hooks/useWaitlist", () => ({
  usePatientWaitlist: () => ({ data: patientWaitlist }),
  useAddToWaitlist: () => ({ mutateAsync: add, isPending: false }),
  useRemoveWaitlistEntry: () => ({ mutateAsync: remove, isPending: false }),
}))

vi.mock("@/components/ui/Toast", () => ({
  useToast: () => ({ showToast }),
}))

function entry(overrides: Partial<WaitlistEntry> = {}): WaitlistEntry {
  return {
    id: "w1",
    patient_id: "p1",
    patient_name: "Rosa Diaz",
    days: ["mon", "wed"],
    times: ["morning"],
    modality: "telehealth",
    session_type: "individual",
    position: 2,
    paused: false,
    no_auto_offer: false,
    created_at: "2026-10-20T15:00:00Z",
    updated_at: "2026-10-20T15:00:00Z",
    ...overrides,
  }
}

function usualTimes(overrides: Partial<Preference> = {}): Preference {
  return {
    id: "pref-1",
    patient_id: "p1",
    domain: "scheduling",
    kind: "scheduling.availability",
    kind_label: "Times that suit",
    value: { days: ["thu", "tue"], times: ["afternoon"], modality: "in_person" },
    text: null,
    strength: "soft",
    summary: null,
    source: "clinician",
    stated_at: "2026-10-12T15:00:00Z",
    confirmed_at: null,
    retired_at: null,
    created_at: "2026-10-12T15:00:00Z",
    updated_at: "2026-10-12T15:00:00Z",
    ...overrides,
  }
}

function renderAction(status = "active") {
  return render(
    <ClientWaitlistAction
      patientId="p1"
      patientName="Rosa Diaz"
      shortName="Rosa"
      patientStatus={status}
    />,
  )
}

function checked(dialog: HTMLElement, role: "checkbox" | "radio"): string[] {
  return within(dialog)
    .getAllByRole(role)
    .filter((input) => (input as HTMLInputElement).checked)
    .map((input) => input.closest("label")?.textContent ?? "")
}

function openDialog() {
  fireEvent.click(screen.getByRole("button", { name: "Add to waitlist" }))
  return screen.getByTestId("waitlist-dialog")
}

describe("ClientWaitlistAction", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    patientWaitlist = { entry: null, usual_session_type: "individual", past_modalities: ["telehealth"] }
    preferences = []
  })

  it("renders nothing until it knows whether the client is on the list", () => {
    patientWaitlist = undefined
    const { container } = renderAction()
    expect(container).toBeEmptyDOMElement()
  })

  it("prefills the usual session type and a single past format", () => {
    renderAction()
    const dialog = openDialog()

    expect(within(dialog).getByRole("radio", { name: "Video" })).toBeChecked()
    expect(within(dialog).getByLabelText("Session type")).toHaveValue("individual")
  })

  it("leaves the format unset when past visits were held both ways", () => {
    patientWaitlist = { entry: null, usual_session_type: null, past_modalities: ["in_person", "telehealth"] }
    renderAction()
    const dialog = openDialog()

    for (const name of ["In person", "Video", "Either"]) {
      expect(within(dialog).getByRole("radio", { name })).not.toBeChecked()
    }
    expect(within(dialog).getByLabelText("Session type")).toHaveValue("")
  })

  it("asks for a day, a time of day and a format before sending anything", async () => {
    patientWaitlist = { entry: null, usual_session_type: null, past_modalities: [] }
    renderAction()
    const dialog = openDialog()

    fireEvent.click(within(dialog).getByTestId("waitlist-save"))

    expect(await within(dialog).findByText("Choose at least one day.")).toBeInTheDocument()
    expect(within(dialog).getByText("Choose at least one time of day.")).toBeInTheDocument()
    expect(within(dialog).getByText("Choose in person, video or either.")).toBeInTheDocument()
    expect(add).not.toHaveBeenCalled()
  })

  it("sends what was chosen", async () => {
    add.mockResolvedValue(entry())
    renderAction()
    const dialog = openDialog()

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Wed" }))
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Mon" }))
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Evening" }))
    fireEvent.click(within(dialog).getByRole("radio", { name: "Either" }))
    fireEvent.click(within(dialog).getByTestId("waitlist-save"))

    await waitFor(() =>
      expect(add).toHaveBeenCalledWith({
        patientId: "p1",
        data: { days: ["mon", "wed"], times: ["evening"], modality: "either", session_type: "individual" },
      }),
    )
  })

  it("shows the server's reason when it refuses", async () => {
    add.mockRejectedValue(
      new ApiError("CONFLICT", "This client is already on your waitlist.", undefined, 409),
    )
    renderAction()
    const dialog = openDialog()

    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Fri" }))
    fireEvent.click(within(dialog).getByRole("checkbox", { name: "Morning" }))
    fireEvent.click(within(dialog).getByTestId("waitlist-save"))

    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      "This client is already on your waitlist.",
    )
  })

  it("disables the action for a client who is not active, and says why", () => {
    renderAction("on_hold")

    const button = screen.getByRole("button", { name: "Add to waitlist" })
    expect(button).toBeDisabled()
    expect(button).toHaveAccessibleDescription("Only active clients can be added to the waitlist.")
  })

  it("shows the client's place on the list and can take them off", async () => {
    patientWaitlist = { entry: entry(), usual_session_type: "individual", past_modalities: [] }
    remove.mockResolvedValue(entry())
    renderAction()

    expect(screen.queryByRole("button", { name: "Add to waitlist" })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole("button", { name: "On your waitlist" }))
    const dialog = screen.getByTestId("waitlist-entry-dialog")
    expect(dialog).toHaveTextContent("Number 2 on your list")
    expect(within(dialog).getByTestId("waitlist-entry-summary")).toHaveTextContent(
      "Mon, Wed · Morning · Video · Individual",
    )
    expect(within(dialog).getByRole("link", { name: "Open the waitlist" })).toHaveAttribute(
      "href",
      "/dashboard/settings/waitlist",
    )

    fireEvent.click(within(dialog).getByRole("button", { name: "Remove from waitlist" }))
    await waitFor(() => expect(remove).toHaveBeenCalledWith("w1"))
  })

  describe("the client's usual times", () => {
    it("start the dialog from them, ahead of the past format", () => {
      preferences = [usualTimes()]
      renderAction()
      const dialog = openDialog()

      expect(checked(dialog, "checkbox")).toEqual(["Tue", "Thu", "Afternoon"])
      expect(within(dialog).getByRole("radio", { name: "In person" })).toBeChecked()
      expect(within(dialog).getByLabelText("Session type")).toHaveValue("individual")
    })

    it("fill in when they arrive after the dialog opens", () => {
      preferences = undefined
      const view = renderAction()
      const dialog = openDialog()
      expect(checked(dialog, "checkbox")).toEqual([])

      preferences = [usualTimes()]
      view.rerender(
        <ClientWaitlistAction
          patientId="p1"
          patientName="Rosa Diaz"
          shortName="Rosa"
          patientStatus="active"
        />,
      )
      expect(checked(dialog, "checkbox")).toEqual(["Tue", "Thu", "Afternoon"])
    })

    it("close the dialog without an offer when the entry matches them", async () => {
      preferences = [usualTimes()]
      add.mockResolvedValue(entry())
      renderAction()
      const dialog = openDialog()

      fireEvent.click(within(dialog).getByTestId("waitlist-save"))

      await waitFor(() => expect(screen.queryByTestId("waitlist-dialog")).not.toBeInTheDocument())
      expect(addPreference).not.toHaveBeenCalled()
      expect(updatePreference).not.toHaveBeenCalled()
    })

    it("can be saved from a new entry with one tap, as a soft preference", async () => {
      add.mockResolvedValue(entry())
      addPreference.mockResolvedValue(usualTimes())
      renderAction()
      const dialog = openDialog()

      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Wed" }))
      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Mon" }))
      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Morning" }))
      fireEvent.click(within(dialog).getByTestId("waitlist-save"))

      const offer = await within(dialog).findByTestId("waitlist-usual-times")
      expect(offer).toHaveTextContent("Rosa Diaz is on your waitlist")
      expect(offer).toHaveTextContent("Mon, Wed · Morning · Video")
      expect(addPreference).not.toHaveBeenCalled()

      fireEvent.click(within(offer).getByRole("button", { name: "Save as Rosa's usual times" }))

      await waitFor(() =>
        expect(addPreference).toHaveBeenCalledWith({
          patientId: "p1",
          data: {
            kind: "scheduling.availability",
            value: { days: ["mon", "wed"], times: ["morning"], modality: "telehealth" },
            strength: "soft",
          },
        }),
      )
      await waitFor(() => expect(screen.queryByTestId("waitlist-dialog")).not.toBeInTheDocument())
      expect(updatePreference).not.toHaveBeenCalled()
    })

    it("update the existing row when the entry differs from them", async () => {
      preferences = [usualTimes({ strength: "hard" })]
      add.mockResolvedValue(entry())
      updatePreference.mockResolvedValue(usualTimes())
      renderAction()
      const dialog = openDialog()

      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Evening" }))
      fireEvent.click(within(dialog).getByTestId("waitlist-save"))
      fireEvent.click(
        await within(dialog).findByRole("button", { name: "Save as Rosa's usual times" }),
      )

      await waitFor(() =>
        expect(updatePreference).toHaveBeenCalledWith({
          patientId: "p1",
          preferenceId: "pref-1",
          data: {
            value: { days: ["tue", "thu"], times: ["afternoon", "evening"], modality: "in_person" },
          },
        }),
      )
      expect(addPreference).not.toHaveBeenCalled()
    })

    it("stay as they were when the offer is closed", async () => {
      preferences = [usualTimes()]
      add.mockResolvedValue(entry())
      renderAction()
      const dialog = openDialog()

      fireEvent.click(within(dialog).getByRole("radio", { name: "Either" }))
      fireEvent.click(within(dialog).getByTestId("waitlist-save"))
      const offer = await within(dialog).findByTestId("waitlist-usual-times")
      fireEvent.click(within(offer).getByRole("button", { name: "Close" }))

      await waitFor(() => expect(screen.queryByTestId("waitlist-dialog")).not.toBeInTheDocument())
      expect(addPreference).not.toHaveBeenCalled()
      expect(updatePreference).not.toHaveBeenCalled()
    })

    it("are not offered when they could not be read", async () => {
      preferences = undefined
      add.mockResolvedValue(entry())
      renderAction()
      const dialog = openDialog()

      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Fri" }))
      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Morning" }))
      fireEvent.click(within(dialog).getByTestId("waitlist-save"))

      await waitFor(() => expect(screen.queryByTestId("waitlist-dialog")).not.toBeInTheDocument())
      expect(addPreference).not.toHaveBeenCalled()
    })

    it("show why when saving them fails, and keep the offer open", async () => {
      add.mockResolvedValue(entry())
      addPreference.mockRejectedValue(new Error("network"))
      renderAction()
      const dialog = openDialog()

      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Sat" }))
      fireEvent.click(within(dialog).getByRole("checkbox", { name: "Morning" }))
      fireEvent.click(within(dialog).getByTestId("waitlist-save"))
      fireEvent.click(
        await within(dialog).findByRole("button", { name: "Save as Rosa's usual times" }),
      )

      expect(await within(dialog).findByRole("alert")).toHaveTextContent(
        "Could not save that change. Please try again.",
      )
      expect(within(dialog).getByTestId("waitlist-usual-times")).toBeInTheDocument()
    })
  })
})
