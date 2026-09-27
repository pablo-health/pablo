// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * RefillRequestForm: the medication choice (listed, or "Something else"),
 * the optional fields, the crisis line above the note, and the body each
 * choice sends.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { PatientRefillMedication } from "@/lib/api/patientRefills"
import { CRISIS_LINE } from "../../messaging/ExpectationNotice"
import { RefillRequestForm } from "../RefillRequestForm"

const MEDS: PatientRefillMedication[] = [
  { id: "med-1", drug_name: "Sertraline", dose: "50 mg" },
  { id: "med-2", drug_name: "Bupropion", dose: "150 mg" },
]

function submitButton(): HTMLButtonElement {
  return screen.getByTestId("portal-refills-submit") as HTMLButtonElement
}

describe("RefillRequestForm", () => {
  it("lists each medication with its dose, plus Something else, in a labelled group", () => {
    render(<RefillRequestForm medications={MEDS} onSubmit={vi.fn()} submitting={false} />)

    const group = screen.getByRole("group", { name: "Medication" })
    expect(group).toBeTruthy()
    expect(screen.getByLabelText("Sertraline 50 mg")).toBeTruthy()
    expect(screen.getByLabelText("Bupropion 150 mg")).toBeTruthy()
    expect(screen.getByLabelText("Something else")).toBeTruthy()
    // The name field waits until "Something else" is picked.
    expect(screen.queryByTestId("portal-refills-medication-text")).toBeNull()
  })

  it("reveals the name field when Something else is picked", async () => {
    const user = userEvent.setup()
    render(<RefillRequestForm medications={MEDS} onSubmit={vi.fn()} submitting={false} />)

    await user.click(screen.getByLabelText("Something else"))

    expect(screen.getByLabelText("Medication name")).toBeTruthy()
  })

  it("shows only the name field when there are no listed medications", () => {
    render(<RefillRequestForm medications={[]} onSubmit={vi.fn()} submitting={false} />)

    expect(screen.queryByRole("group", { name: "Medication" })).toBeNull()
    expect(screen.queryAllByRole("radio")).toHaveLength(0)
    expect(screen.getByLabelText("Medication name")).toBeTruthy()
  })

  it("puts the crisis line above the note", () => {
    render(<RefillRequestForm medications={MEDS} onSubmit={vi.fn()} submitting={false} />)

    const crisis = screen.getByText(CRISIS_LINE)
    const note = screen.getByLabelText("Anything your prescriber should know?")
    expect(
      crisis.compareDocumentPosition(note) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy()
  })

  it("stays disabled until a medication is chosen", async () => {
    const user = userEvent.setup()
    render(<RefillRequestForm medications={MEDS} onSubmit={vi.fn()} submitting={false} />)

    expect(submitButton().disabled).toBe(true)
    await user.click(screen.getByLabelText("Something else"))
    expect(submitButton().disabled).toBe(true)
    await user.type(screen.getByLabelText("Medication name"), "Lamotrigine")
    expect(submitButton().disabled).toBe(false)
  })

  it("sends the listed medication's id and no typed name", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<RefillRequestForm medications={MEDS} onSubmit={onSubmit} submitting={false} />)

    await user.click(screen.getByLabelText("Bupropion 150 mg"))
    await user.type(screen.getByTestId("portal-refills-pharmacy"), " Main St Pharmacy ")
    await user.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith({
      medication_id: "med-2",
      medication_text: null,
      pharmacy_text: "Main St Pharmacy",
      patient_note: null,
    })
  })

  it("sends the typed name and no id for Something else", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<RefillRequestForm medications={MEDS} onSubmit={onSubmit} submitting={false} />)

    await user.click(screen.getByLabelText("Something else"))
    await user.type(screen.getByLabelText("Medication name"), "Lamotrigine 25 mg")
    await user.type(
      screen.getByLabelText("Anything your prescriber should know?"),
      "Running low",
    )
    await user.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith({
      medication_id: null,
      medication_text: "Lamotrigine 25 mg",
      pharmacy_text: null,
      patient_note: "Running low",
    })
  })

  it("sends the typed name when there is no list", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<RefillRequestForm medications={[]} onSubmit={onSubmit} submitting={false} />)

    await user.type(screen.getByLabelText("Medication name"), "Lamotrigine")
    await user.click(submitButton())

    expect(onSubmit).toHaveBeenCalledWith({
      medication_id: null,
      medication_text: "Lamotrigine",
      pharmacy_text: null,
      patient_note: null,
    })
  })

  it("clears the form after a successful send", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(<RefillRequestForm medications={MEDS} onSubmit={onSubmit} submitting={false} />)

    await user.click(screen.getByLabelText("Sertraline 50 mg"))
    await user.type(screen.getByTestId("portal-refills-note"), "Thanks")
    await user.click(submitButton())

    await waitFor(() =>
      expect((screen.getByLabelText("Sertraline 50 mg") as HTMLInputElement).checked).toBe(
        false,
      ),
    )
    expect((screen.getByTestId("portal-refills-note") as HTMLTextAreaElement).value).toBe("")
  })

  it("keeps what was entered when the send fails, and shows the error it is given", async () => {
    const onSubmit = vi.fn().mockRejectedValue(new Error("nope"))
    const user = userEvent.setup()
    const { rerender } = render(
      <RefillRequestForm medications={MEDS} onSubmit={onSubmit} submitting={false} />,
    )

    await user.click(screen.getByLabelText("Sertraline 50 mg"))
    await user.click(submitButton())
    await waitFor(() => expect(onSubmit).toHaveBeenCalled())

    rerender(
      <RefillRequestForm
        medications={MEDS}
        onSubmit={onSubmit}
        submitting={false}
        error="That didn't send. Try again."
      />,
    )
    expect((screen.getByLabelText("Sertraline 50 mg") as HTMLInputElement).checked).toBe(true)
    expect(screen.getByTestId("portal-refills-error").textContent).toBe(
      "That didn't send. Try again.",
    )
  })

  it("holds still while a request is in flight", () => {
    render(<RefillRequestForm medications={MEDS} onSubmit={vi.fn()} submitting={true} />)

    expect(submitButton().disabled).toBe(true)
    expect(submitButton().textContent).toBe("Sending…")
  })
})
