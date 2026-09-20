// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Reading a consent document and typing a name against it.
 *
 * The client is mocked so the assertions can be about the screen rather than
 * about HTTP, but `PatientIntakeError` stays the real class — the renderer
 * branches on its kind, and a stand-in would let a wrong branch pass.
 *
 * What is worth proving here is the half the server cannot: that the words
 * on screen are the ones the server rendered, that Sign is not offered until
 * somebody has both ticked the box and typed a name, that a signature reads
 * back after the fact, and that a document needing a newer version says so
 * instead of leaving somebody pressing a button that will not work.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import * as api from "@/lib/api/patientIntake"
import { PatientIntakeError } from "@/lib/api/patientIntake"
import type { IntakeAssignmentItem, IntakeSignature } from "@/lib/api/patientIntake"
import { rendererFor } from "../renderers/registry"
import {
  CONSENT_AWAITING_GUARDIAN,
  CONSENT_NEEDS_RESIGN,
  CONSENT_SIGNED_BADGE,
} from "../formsCopy"

vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  return {
    ...actual,
    fetchConsentDocument: vi.fn(),
    listSignatures: vi.fn(),
    signConsentDocument: vi.fn(),
  }
})

const ASSIGNMENT_ID = "00000000-0000-4000-8000-00000000000a"
const ITEM_ID = "00000000-0000-4000-8000-00000000000b"
const VERSION_ID = "00000000-0000-4000-8000-00000000000c"
const SESSION = "session-token"

const STATEMENT =
  "By typing my name I agree that this is my electronic signature and that I have read this document."

const DOCUMENT_TITLE = "Consent to treatment"
const DOCUMENT_SENTENCE = "You are agreeing to be treated here."

function document(overrides: Partial<api.PatientConsentDocument> = {}) {
  return {
    id: VERSION_ID,
    document_key: "00000000-0000-4000-8000-00000000000d",
    title: DOCUMENT_TITLE,
    // No heading of its own: the screen puts the document's title above the
    // text, so a body that repeated it would be shown twice. Practices whose
    // markdown does repeat it get exactly that, which is their text and not
    // this component's to second-guess.
    rendered_html: `<p>${DOCUMENT_SENTENCE}</p>`,
    version: 1,
    digest: "a".repeat(64),
    requires_signature: true,
    signer_roles: ["patient"],
    consent_statement: STATEMENT,
    consent_statement_version: "1",
    ...overrides,
  }
}

function signature(overrides: Partial<IntakeSignature> = {}): IntakeSignature {
  return {
    id: "00000000-0000-4000-8000-00000000000e",
    assignment_id: ASSIGNMENT_ID,
    item_id: ITEM_ID,
    document_version_id: VERSION_ID,
    document_digest: "a".repeat(64),
    signer_role: "patient",
    signer_typed_name: "Ada Lovelace",
    consent_statement_version: "1",
    consent_statement: STATEMENT,
    signed_at: "2026-09-20T14:30:00+00:00",
    auth_strength: "stepped_up",
    session_id: "session-handle",
    evidence_digest: "b".repeat(64),
    ...overrides,
  }
}

function consentItem(overrides: Partial<IntakeAssignmentItem> = {}): IntakeAssignmentItem {
  return {
    id: ITEM_ID,
    key: "consent",
    position: 0,
    item_type: "consent_document",
    required: true,
    label: null,
    help_text: null,
    config: {
      document_key: "00000000-0000-4000-8000-00000000000d",
      document_version_id: VERSION_ID,
    },
    value: null,
    ...overrides,
  }
}

function renderConsent(item: IntakeAssignmentItem = consentItem()) {
  const onWrote = vi.fn()
  const onSessionLost = vi.fn()
  const renderer = rendererFor("consent_document")
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
        onWrote={onWrote}
        onSessionLost={onSessionLost}
      />
    </QueryClientProvider>,
  )
  return { onWrote, onSessionLost }
}

const fetchConsentDocument = vi.mocked(api.fetchConsentDocument)
const listSignatures = vi.mocked(api.listSignatures)
const signConsentDocument = vi.mocked(api.signConsentDocument)

beforeEach(() => {
  vi.clearAllMocks()
  fetchConsentDocument.mockResolvedValue(document())
  listSignatures.mockResolvedValue([])
  signConsentDocument.mockResolvedValue(signature())
})

describe("reading the document", () => {
  it("shows the words the server rendered", async () => {
    renderConsent()

    expect(await screen.findByTestId("forms-consent-document")).toHaveTextContent(
      DOCUMENT_SENTENCE,
    )
    expect(screen.getByRole("heading", { name: DOCUMENT_TITLE })).toBeInTheDocument()
  })

  it("asks the version the form pinned, not whatever is newest", async () => {
    renderConsent()

    await screen.findByTestId("forms-consent-document")
    expect(fetchConsentDocument).toHaveBeenCalledWith(SESSION, VERSION_ID)
  })

  it("shows the consent statement the server sent", async () => {
    // Served rather than written in the front end, because the version of it
    // is what a signature records.
    renderConsent()

    expect(await screen.findByTestId("forms-consent-statement")).toHaveTextContent(STATEMENT)
  })

  it("prefers the practice's own wording for the heading when it wrote one", async () => {
    renderConsent(consentItem({ label: "Please read and sign this" }))

    expect(
      await screen.findByRole("heading", { name: "Please read and sign this" }),
    ).toBeInTheDocument()
  })

  it("reads as a step still to come when the form pinned no version", async () => {
    // The publisher prevents this, and the server refuses a signature for it
    // — so the screen says the honest thing rather than offering a button.
    renderConsent(consentItem({ config: { document_key: "doc" } }))

    expect(await screen.findByTestId("forms-item-unavailable")).toBeInTheDocument()
    expect(fetchConsentDocument).not.toHaveBeenCalled()
  })

  it("says so when the document will not load", async () => {
    fetchConsentDocument.mockRejectedValue(new PatientIntakeError("unavailable", "down"))
    renderConsent()

    expect(await screen.findByTestId("forms-consent-error")).toBeInTheDocument()
  })
})

describe("signing", () => {
  it("does not offer Sign until the box is ticked and a name is typed", async () => {
    const user = userEvent.setup()
    renderConsent()

    const button = await screen.findByTestId("forms-consent-sign")
    expect(button).toBeDisabled()

    await user.click(screen.getByTestId("forms-consent-affirm"))
    expect(button).toBeDisabled()

    await user.type(screen.getByTestId("forms-consent-name"), "Ada Lovelace")
    expect(button).toBeEnabled()
  })

  it("stays refused when the name is only spaces", async () => {
    const user = userEvent.setup()
    renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "   ")

    expect(screen.getByTestId("forms-consent-sign")).toBeDisabled()
  })

  it("sends the item, the role, the trimmed name and the affirmation", async () => {
    const user = userEvent.setup()
    renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "  Ada Lovelace  ")
    await user.click(screen.getByTestId("forms-consent-sign"))

    await waitFor(() =>
      expect(signConsentDocument).toHaveBeenCalledWith(SESSION, ASSIGNMENT_ID, {
        item_id: ITEM_ID,
        signer_role: "patient",
        typed_name: "Ada Lovelace",
        affirm: true,
      }),
    )
  })

  it("tells the walk to re-read once the signature lands", async () => {
    // The walk asks the server what is still outstanding rather than
    // working it out here.
    const user = userEvent.setup()
    const { onWrote } = renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "Ada Lovelace")
    await user.click(screen.getByTestId("forms-consent-sign"))

    await waitFor(() => expect(onWrote).toHaveBeenCalled())
  })

  it("hands a dead session back to the shell", async () => {
    const user = userEvent.setup()
    signConsentDocument.mockRejectedValue(new PatientIntakeError("expired", "gone"))
    const { onSessionLost } = renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "Ada Lovelace")
    await user.click(screen.getByTestId("forms-consent-sign"))

    await waitFor(() => expect(onSessionLost).toHaveBeenCalled())
  })

  it("shows the server's own sentence when it refuses", async () => {
    const user = userEvent.setup()
    signConsentDocument.mockRejectedValue(
      new PatientIntakeError("invalid", "Refused", {
        serverMessage: "Type your name to sign this.",
      }),
    )
    renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "Ada Lovelace")
    await user.click(screen.getByTestId("forms-consent-sign"))

    expect(await screen.findByTestId("forms-consent-error-message")).toHaveTextContent(
      "Type your name to sign this.",
    )
  })
})

describe("once it is signed", () => {
  it("shows who signed, when, and a badge", async () => {
    listSignatures.mockResolvedValue([signature()])
    renderConsent(consentItem({ value: { signed: true, signature_id: "sig" } }))

    const signed = await screen.findByTestId("forms-consent-signed")
    expect(signed).toHaveTextContent("Ada Lovelace")
    expect(signed).toHaveTextContent(CONSENT_SIGNED_BADGE)
  })

  it("stops offering the form to sign again", async () => {
    listSignatures.mockResolvedValue([signature()])
    renderConsent(consentItem({ value: { signed: true, signature_id: "sig" } }))

    await screen.findByTestId("forms-consent-signed")
    expect(screen.queryByTestId("forms-consent-sign")).not.toBeInTheDocument()
  })

  it("reads back after a reload, not only for the browser that signed", async () => {
    // The list comes from the server, so a form signed yesterday on another
    // device still looks signed.
    listSignatures.mockResolvedValue([signature()])
    renderConsent()

    await screen.findByTestId("forms-consent-signed")
    expect(listSignatures).toHaveBeenCalledWith(SESSION, ASSIGNMENT_ID)
  })

  it("ignores a signature belonging to another question on the same form", async () => {
    listSignatures.mockResolvedValue([signature({ item_id: "some-other-item" })])
    renderConsent()

    await screen.findByTestId("forms-consent-sign")
    expect(screen.queryByTestId("forms-consent-signed")).not.toBeInTheDocument()
  })
})

describe("a document that asks for two signatures", () => {
  it("offers the choice and says who a guardian is signing for", async () => {
    fetchConsentDocument.mockResolvedValue(
      document({ signer_roles: ["patient", "guardian"] }),
    )
    renderConsent()

    const roles = await screen.findByTestId("forms-consent-role")
    expect(roles).toHaveTextContent("I'm signing for myself")
    expect(roles).toHaveTextContent("I'm signing for the patient")
    expect(roles).toHaveTextContent("enter your own name")
  })

  it("says what is still outstanding after the first one", async () => {
    fetchConsentDocument.mockResolvedValue(
      document({ signer_roles: ["patient", "guardian"] }),
    )
    listSignatures.mockResolvedValue([signature()])
    renderConsent()

    expect(await screen.findByTestId("forms-consent-outstanding")).toHaveTextContent(
      CONSENT_AWAITING_GUARDIAN,
    )
    expect(screen.getByTestId("forms-consent-sign")).toBeInTheDocument()
  })

  it("signs as the role that is still outstanding", async () => {
    const user = userEvent.setup()
    fetchConsentDocument.mockResolvedValue(
      document({ signer_roles: ["patient", "guardian"] }),
    )
    listSignatures.mockResolvedValue([signature()])
    renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "Mary Somerville")
    await user.click(screen.getByTestId("forms-consent-sign"))

    await waitFor(() =>
      expect(signConsentDocument).toHaveBeenCalledWith(
        SESSION,
        ASSIGNMENT_ID,
        expect.objectContaining({ signer_role: "guardian", typed_name: "Mary Somerville" }),
      ),
    )
  })

  it("is finished when both have signed", async () => {
    fetchConsentDocument.mockResolvedValue(
      document({ signer_roles: ["patient", "guardian"] }),
    )
    listSignatures.mockResolvedValue([
      signature(),
      signature({
        id: "00000000-0000-4000-8000-00000000000f",
        signer_role: "guardian",
        signer_typed_name: "Mary Somerville",
      }),
    ])
    renderConsent()

    const signed = await screen.findByTestId("forms-consent-signed")
    expect(signed).toHaveTextContent("Ada Lovelace")
    expect(signed).toHaveTextContent("Mary Somerville")
    expect(screen.queryByTestId("forms-consent-sign")).not.toBeInTheDocument()
    expect(screen.queryByTestId("forms-consent-outstanding")).not.toBeInTheDocument()
  })
})

describe("a newer version of the document", () => {
  it("explains that a fresh signature is needed instead of leaving the button", async () => {
    const user = userEvent.setup()
    signConsentDocument.mockRejectedValue(
      new PatientIntakeError("closed", "Form closed", {
        serverMessage: "There is a newer version of this document to read and sign.",
      }),
    )
    renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "Ada Lovelace")
    await user.click(screen.getByTestId("forms-consent-sign"))

    expect(await screen.findByTestId("forms-consent-resign")).toHaveTextContent(
      CONSENT_NEEDS_RESIGN,
    )
    expect(screen.queryByTestId("forms-consent-sign")).not.toBeInTheDocument()
  })

  it("leaves another 409 as the server's own sentence", async () => {
    // "Already signed" and "not ready to sign" are both 409s, and neither
    // means a new version is waiting.
    const user = userEvent.setup()
    signConsentDocument.mockRejectedValue(
      new PatientIntakeError("closed", "Form closed", {
        serverMessage: "This has already been signed.",
      }),
    )
    renderConsent()

    await user.click(await screen.findByTestId("forms-consent-affirm"))
    await user.type(screen.getByTestId("forms-consent-name"), "Ada Lovelace")
    await user.click(screen.getByTestId("forms-consent-sign"))

    expect(await screen.findByTestId("forms-consent-error-message")).toHaveTextContent(
      "This has already been signed.",
    )
    expect(screen.queryByTestId("forms-consent-resign")).not.toBeInTheDocument()
  })
})

describe("the review row", () => {
  it("says nothing until the server says the item is signed", () => {
    const renderer = rendererFor("consent_document")

    expect(renderer.summary(null, consentItem(), null)).toBeNull()
    // Half-signed: the server says the item is not settled, so neither does
    // this row.
    expect(
      renderer.summary({ signed: false, signature_id: "sig" }, consentItem(), null),
    ).toBeNull()
    expect(
      renderer.summary({ signed: true, signature_id: "sig" }, consentItem(), null),
    ).toBe(CONSENT_SIGNED_BADGE)
  })
})
