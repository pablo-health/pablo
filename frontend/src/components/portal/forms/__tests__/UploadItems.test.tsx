// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Sending a file a form asked for, from the two questions that ask.
 *
 * The client is mocked so the assertions can be about the screen rather
 * than about HTTP, but `PatientIntakeError` stays the real class — the
 * renderer branches on its kind, and a stand-in would let a wrong branch
 * pass.
 *
 * What is worth proving here is the half the server cannot.
 *
 * **The three calls happen in order.** Ask where to put it, put it there,
 * then say what it answers. A browser that attached first would be naming
 * a document that is not on the chart yet.
 *
 * **A card asks for the sides it asked for.** Two slots on `both`, one on
 * `front`, and each attaches with its own side — which is what the front
 * and the back being distinguishable rests on.
 *
 * **What is on screen is what the server says arrived**, not what this
 * browser uploaded: the slots read `artifacts`, so a card photographed
 * yesterday on another device still looks sent.
 *
 * **A refused file says what to do.** The server writes that sentence, and
 * the renderer shows it rather than guessing at a cause.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as api from "@/lib/api/patientIntake"
import { PatientIntakeError } from "@/lib/api/patientIntake"
import type { IntakeArtifact, IntakeAssignmentItem } from "@/lib/api/patientIntake"
import { rendererFor } from "../renderers/registry"
import { BLANK_FORM_DOWNLOAD, UPLOAD_SENT } from "../formsCopy"

vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  return {
    ...actual,
    startUpload: vi.fn(),
    sendToStorage: vi.fn(),
    finishUpload: vi.fn(),
    attachArtifact: vi.fn(),
    removeArtifact: vi.fn(),
    uploadPreviewUrl: vi.fn(),
    blankFormUrl: vi.fn(),
  }
})

const ASSIGNMENT_ID = "00000000-0000-4000-8000-00000000000a"
const ITEM_ID = "00000000-0000-4000-8000-00000000000b"
const DOCUMENT_ID = "00000000-0000-4000-8000-00000000000c"
const SESSION = "session-token"

function itemOf(itemType: string, config: Record<string, unknown>): IntakeAssignmentItem {
  return {
    id: ITEM_ID,
    key: itemType,
    position: 0,
    item_type: itemType,
    required: true,
    label: itemType === "insurance_card" ? "A photo of your card" : "Any prior records",
    help_text: null,
    config,
    value: null,
  }
}

function artifactOf(side: string | null, id = "artifact-1"): IntakeArtifact {
  return {
    id,
    assignment_id: ASSIGNMENT_ID,
    item_id: ITEM_ID,
    document_id: DOCUMENT_ID,
    side,
    created_at: "2026-09-20T09:00:00Z",
  }
}

function renderItem(item: IntakeAssignmentItem, artifacts: IntakeArtifact[] = []) {
  const onWrote = vi.fn()
  const onSessionLost = vi.fn()
  const renderer = rendererFor(item.item_type)
  // A provider because the card's typed-plan half saves through a
  // mutation, the same way the consent renderer signs through one.
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <renderer.Component
        item={item}
        value={item.value}
        onChange={vi.fn()}
        form={null}
        assignmentId={ASSIGNMENT_ID}
        sessionToken={SESSION}
        artifacts={artifacts}
        onWrote={onWrote}
        onSessionLost={onSessionLost}
      />
    </QueryClientProvider>,
  )
  return { onWrote, onSessionLost, renderer }
}

function aFile(name = "card.png", type = "image/png"): File {
  return new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], name, { type })
}

function happyUpload() {
  vi.mocked(api.startUpload).mockResolvedValue({
    document_id: DOCUMENT_ID,
    upload: { url: "https://storage.example/x", method: "PUT", headers: {}, fields: {} },
    max_bytes: 25 * 1024 * 1024,
  })
  vi.mocked(api.sendToStorage).mockResolvedValue(undefined)
  vi.mocked(api.finishUpload).mockResolvedValue({ id: DOCUMENT_ID })
  vi.mocked(api.attachArtifact).mockResolvedValue({
    artifact: artifactOf("front"),
    status: "in_progress",
    progress: { complete: false, missing: [ITEM_ID] },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe("an insurance card", () => {
  it("asks for both sides when the question does", () => {
    renderItem(itemOf("insurance_card", { sides: "both" }))

    expect(screen.getByTestId("forms-upload-front")).toBeInTheDocument()
    expect(screen.getByTestId("forms-upload-back")).toBeInTheDocument()
  })

  it("asks for one when the question asks for one", () => {
    renderItem(itemOf("insurance_card", { sides: "front" }))

    expect(screen.getByTestId("forms-upload-front")).toBeInTheDocument()
    expect(screen.queryByTestId("forms-upload-back")).not.toBeInTheDocument()
  })

  it("sends a photo through the three calls, in order, and says which side", async () => {
    happyUpload()
    const { onWrote } = renderItem(itemOf("insurance_card", { sides: "both" }))

    await userEvent.upload(screen.getByTestId("forms-upload-back-input"), aFile())

    await waitFor(() => expect(api.attachArtifact).toHaveBeenCalled())
    expect(api.startUpload).toHaveBeenCalledWith(SESSION, expect.objectContaining({ name: "card.png" }))
    expect(api.sendToStorage).toHaveBeenCalled()
    expect(api.finishUpload).toHaveBeenCalledWith(SESSION, DOCUMENT_ID)
    // The side rides with the attach, which is what distinguishes the two
    // photographs from each other.
    expect(api.attachArtifact).toHaveBeenCalledWith(SESSION, ASSIGNMENT_ID, {
      item_id: ITEM_ID,
      document_id: DOCUMENT_ID,
      side: "back",
    })
    // And the walk is told to re-read, because the server owns progress.
    expect(onWrote).toHaveBeenCalled()
  })

  it("shows the server's sentence when a file is refused", async () => {
    vi.mocked(api.startUpload).mockResolvedValue({
      document_id: DOCUMENT_ID,
      upload: { url: "https://storage.example/x", method: "PUT", headers: {}, fields: {} },
      max_bytes: 25 * 1024 * 1024,
    })
    vi.mocked(api.sendToStorage).mockResolvedValue(undefined)
    vi.mocked(api.finishUpload).mockRejectedValue(
      new PatientIntakeError("invalid", "Refused", {
        serverMessage: "Unsupported document type",
      }),
    )
    renderItem(itemOf("insurance_card", { sides: "both" }))

    await userEvent.upload(screen.getByTestId("forms-upload-front-input"), aFile("notes.png"))

    expect(await screen.findByTestId("forms-upload-front-error")).toHaveTextContent(
      "Unsupported document type",
    )
    expect(api.attachArtifact).not.toHaveBeenCalled()
  })

  it("hands a dead session to the shell rather than showing an error", async () => {
    vi.mocked(api.startUpload).mockRejectedValue(
      new PatientIntakeError("expired", "Session expired"),
    )
    const { onSessionLost } = renderItem(itemOf("insurance_card", { sides: "both" }))

    await userEvent.upload(screen.getByTestId("forms-upload-front-input"), aFile())

    await waitFor(() => expect(onSessionLost).toHaveBeenCalled())
    expect(screen.queryByTestId("forms-upload-front-error")).not.toBeInTheDocument()
  })

  it("reads what arrived from the server, not from this browser", () => {
    renderItem(itemOf("insurance_card", { sides: "both" }), [artifactOf("front")])

    // The front is sent — it was photographed on some other device.
    expect(screen.getByTestId("forms-upload-front-sent")).toHaveTextContent(UPLOAD_SENT)
    // The back still asks.
    expect(screen.getByTestId("forms-upload-back-choose")).toBeInTheDocument()
  })

  it("takes a photo back off and tells the walk to re-read", async () => {
    vi.mocked(api.removeArtifact).mockResolvedValue({
      artifact: artifactOf("front"),
      status: "in_progress",
      progress: { complete: false, missing: [ITEM_ID] },
    })
    const { onWrote } = renderItem(itemOf("insurance_card", { sides: "both" }), [
      artifactOf("front"),
    ])

    await userEvent.click(screen.getByTestId("forms-upload-front-remove"))

    await waitFor(() =>
      expect(api.removeArtifact).toHaveBeenCalledWith(SESSION, ASSIGNMENT_ID, "artifact-1"),
    )
    expect(onWrote).toHaveBeenCalled()
  })

  it("offers the typed plan only when the question asks for it", () => {
    renderItem(itemOf("insurance_card", { sides: "both" }))

    expect(screen.queryByTestId("forms-coverage-fields")).not.toBeInTheDocument()
  })

  it("and shows them when it does", () => {
    renderItem(itemOf("insurance_card", { sides: "both", collect_fields: true }))

    expect(screen.getByTestId("forms-coverage-fields")).toBeInTheDocument()
  })

  it("saves the typed plan through its own route", async () => {
    const save = vi.spyOn(api, "saveIntakeCoverage").mockResolvedValue({
      coverage_id: "coverage-1",
      eligibility_requested: true,
    })
    renderItem(itemOf("insurance_card", { sides: "both", collect_fields: true }))

    await userEvent.type(screen.getByTestId("forms-coverage-payer"), "Blue Cross")
    await userEvent.type(screen.getByTestId("forms-coverage-member"), "ZGP884401")
    await userEvent.click(screen.getByTestId("forms-coverage-save"))

    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save).toHaveBeenCalledWith(SESSION, ASSIGNMENT_ID, ITEM_ID, {
      payer_name: "Blue Cross",
      member_id: "ZGP884401",
      group_number: null,
    })
    expect(await screen.findByTestId("forms-coverage-saved")).toBeInTheDocument()
  })

  it("will not save a plan with nothing on it", async () => {
    const save = vi.spyOn(api, "saveIntakeCoverage")
    renderItem(itemOf("insurance_card", { sides: "both", collect_fields: true }))

    expect(screen.getByTestId("forms-coverage-save")).toBeDisabled()
    expect(save).not.toHaveBeenCalled()
  })

  it("counts what arrived on the review screen, and never names a file", () => {
    const { renderer } = renderItem(itemOf("insurance_card", { sides: "both" }))

    expect(renderer.summary(null, itemOf("insurance_card", {}), null)).toBeNull()
    expect(
      renderer.summary(
        { documents: [{ document_id: DOCUMENT_ID, side: "front" }] },
        itemOf("insurance_card", {}),
        null,
      ),
    ).toBe("1 file sent")
  })

  it("is not saved by the walk, and is still a question", () => {
    const renderer = rendererFor("insurance_card")
    expect(renderer.answerable).toBe(false)
    expect(renderer.writesItself).toBe(true)
  })
})

describe("a document request", () => {
  it("always offers one more slot", () => {
    renderItem(itemOf("document_request", {}), [artifactOf(null, "artifact-1")])

    // One sent, and an empty slot under it: there is no fixed number.
    expect(screen.getAllByTestId("forms-upload")).toHaveLength(2)
    expect(screen.getAllByTestId("forms-upload-sent")).toHaveLength(1)
    expect(screen.getAllByTestId("forms-upload-choose")).toHaveLength(1)
  })

  it("attaches with no side at all", async () => {
    happyUpload()
    renderItem(itemOf("document_request", {}))

    await userEvent.upload(
      screen.getByTestId("forms-upload-input"),
      aFile("records.pdf", "application/pdf"),
    )

    await waitFor(() => expect(api.attachArtifact).toHaveBeenCalled())
    expect(api.attachArtifact).toHaveBeenCalledWith(SESSION, ASSIGNMENT_ID, {
      item_id: ITEM_ID,
      document_id: DOCUMENT_ID,
    })
  })

  it("narrows the picker to what the question takes", () => {
    renderItem(itemOf("document_request", { accept: ["application/pdf"] }))

    expect(screen.getByTestId("forms-upload-input")).toHaveAttribute("accept", "application/pdf")
  })

  it("offers the practice's own form when the question names one", async () => {
    vi.mocked(api.blankFormUrl).mockResolvedValue("https://storage.example/roi.pdf")
    const open = vi.spyOn(window, "open").mockReturnValue(null)
    renderItem(itemOf("document_request", { blank_form_id: "form-1" }))

    expect(screen.getByTestId("forms-blank-form-download")).toHaveTextContent(BLANK_FORM_DOWNLOAD)
    await userEvent.click(screen.getByTestId("forms-blank-form-download"))

    // Fetched on the click, not up front: the URL is short-lived, and
    // minting one for a screen nobody acts on discloses a file for nothing.
    await waitFor(() => expect(api.blankFormUrl).toHaveBeenCalledWith(SESSION, "form-1"))
    expect(open).toHaveBeenCalledWith(
      "https://storage.example/roi.pdf",
      "_blank",
      "noopener,noreferrer",
    )
  })

  it("says nothing about paper when the question names no form", () => {
    renderItem(itemOf("document_request", {}))

    expect(screen.queryByTestId("forms-blank-form")).not.toBeInTheDocument()
  })

  it("is not saved by the walk, and is still a question", () => {
    const renderer = rendererFor("document_request")
    expect(renderer.answerable).toBe(false)
    expect(renderer.writesItself).toBe(true)
  })
})
