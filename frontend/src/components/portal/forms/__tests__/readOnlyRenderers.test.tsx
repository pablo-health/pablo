// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every renderer, drawn read-only: the way the chart shows a handed-in form.
 *
 * Three things must hold for each type. The recorded answer is on screen,
 * marked where the patient marked it. Nothing on screen changes it — every
 * control is disabled or read-only, and a read-only draw is handed no
 * `onChange` or session at all, which the props type enforces; clicking the
 * whole screen must still not throw. And nothing reaches a patient route: a
 * clinician drawing the form has no patient session, so the two renderers
 * that read through one read what `readOnly` hands them instead.
 *
 * The first test walks the registry, so a new item type is drawn read-only
 * here before it can be drawn anywhere else.
 */

import { beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import * as patientApi from "@/lib/api/patientIntake"
import type { IntakeArtifact, IntakeAssignmentItem, IntakeSignature } from "@/lib/api/patientIntake"
import { renderWithProviders } from "@/test/renderWithProviders"
import { RENDERED_ITEM_TYPES, rendererFor } from "../renderers/registry"
import type { AnswerValue, ReadOnlySource } from "../renderers/types"
import { INTAKE_FORM } from "./formFixtures"

// A patient route reached from a read-only draw is the bug this file exists
// to catch, so every one of them fails loudly.
vi.mock("@/lib/api/patientIntake", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api/patientIntake")>()
  const refuse = () => {
    throw new Error("a read-only renderer called a patient route")
  }
  return {
    ...actual,
    fetchConsentDocument: vi.fn(refuse),
    listSignatures: vi.fn(refuse),
    signConsentDocument: vi.fn(refuse),
    startUpload: vi.fn(refuse),
    removeArtifact: vi.fn(refuse),
    uploadPreviewUrl: vi.fn(refuse),
    blankFormUrl: vi.fn(refuse),
    saveIntakeCoverage: vi.fn(refuse),
  }
})

const SIGNATURE: IntakeSignature = {
  id: "sig-1",
  assignment_id: "assign-1",
  item_id: "item-1",
  document_version_id: "docv-1",
  document_digest: "digest",
  signer_role: "patient",
  signer_typed_name: "Dana Okonkwo",
  consent_statement_version: "1",
  consent_statement: "I have read this document and agree to it.",
  signed_at: "2026-03-14T12:00:00Z",
  auth_strength: "stepped_up",
  session_id: null,
  evidence_digest: "evidence",
}

function artifact(overrides: Partial<IntakeArtifact>): IntakeArtifact {
  return {
    id: "art-1",
    assignment_id: "assign-1",
    item_id: "item-1",
    document_id: "doc-1",
    side: null,
    created_at: "2026-03-14T12:00:00Z",
    ...overrides,
  }
}

const PATIENT_ROUTES = [
  "fetchConsentDocument",
  "listSignatures",
  "signConsentDocument",
  "startUpload",
  "removeArtifact",
  "uploadPreviewUrl",
  "blankFormUrl",
  "saveIntakeCoverage",
] as const

let source: ReadOnlySource
let fetchSpy: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.clearAllMocks()
  // Anything that reaches the network at all, by any client, lands here.
  fetchSpy = vi.fn(() => Promise.reject(new Error("a read-only renderer fetched")))
  globalThis.fetch = fetchSpy as unknown as typeof fetch
  source = {
    signatures: [SIGNATURE],
    loadDocument: vi.fn(async () => ({ title: "Consent to treatment", rendered_html: "<p>We keep records private.</p>" })),
    filenames: { "art-1": "front.jpg", "art-2": "back.jpg" },
    openFile: vi.fn(async () => {}),
  }
})

function item(itemType: string, config: Record<string, unknown> = {}, label: string | null = "The question"): IntakeAssignmentItem {
  return {
    id: "item-1",
    key: itemType,
    position: 0,
    item_type: itemType,
    required: true,
    label,
    help_text: null,
    config,
    value: null,
  }
}

function draw(shown: IntakeAssignmentItem, value: AnswerValue | null, artifacts: IntakeArtifact[] = []) {
  const Renderer = rendererFor(shown.item_type).Component
  const view = renderWithProviders(
    <Renderer item={shown} value={value} form={INTAKE_FORM} artifacts={artifacts} readOnly={source} />,
  )
  return { container: view.container }
}

/** Click everything a person could click, and prove none of it wrote. */
async function clickEverything(container: HTMLElement) {
  for (const control of container.querySelectorAll("input, button, label, textarea")) {
    if (control.textContent === "View") continue
    await userEvent.click(control as HTMLElement).catch(() => undefined)
  }
}

function expectLocked(container: HTMLElement) {
  for (const input of container.querySelectorAll("input, textarea, select")) {
    const element = input as HTMLInputElement
    expect(element.disabled || element.readOnly, `${element.outerHTML} is locked`).toBe(true)
  }
}

/** One answer for every type the portal draws, and what it must show. */
const CASES: Record<string, { item: IntakeAssignmentItem; value: AnswerValue | null; artifacts?: IntakeArtifact[]; expect: () => void | Promise<void> }> = {
  demographics: {
    item: item("demographics", {}, null),
    value: { name_confirmed: false, dob_confirmed: false, corrections: "Middle name is Ada." },
    expect: () => {
      expect(screen.getByText("Dana Okonkwo")).toBeInTheDocument()
      expect(screen.getByTestId("forms-identity-deny")).toHaveAttribute("aria-pressed", "true")
      expect(screen.getByTestId("forms-identity-confirm")).toHaveAttribute("aria-pressed", "false")
      expect(screen.getByTestId("forms-corrections")).toHaveTextContent("Middle name is Ada.")
    },
  },
  reason: {
    item: item("reason", {}, null),
    value: { text: "Panic before every shift." },
    expect: () => {
      expect(screen.getByText("What brings you in?")).toBeInTheDocument()
      expect(screen.getByTestId("forms-reason")).toHaveTextContent("Panic before every shift.")
    },
  },
  instrument: {
    item: item("instrument", { code: "gad7" }, null),
    value: { item_scores: { "1": 3, "2": 0, "3": 1, "4": 2, "5": 0, "6": 0, "7": 1 } },
    expect: () => {
      const first = screen.getByRole("group", { name: "Feeling nervous, anxious, or on edge" })
      expect(within(first).getByRole("radio", { name: "Nearly every day" })).toBeChecked()
      expect(within(first).getByRole("radio", { name: "Not at all" })).not.toBeChecked()
      const fourth = screen.getByRole("group", { name: "Trouble relaxing" })
      expect(within(fourth).getByRole("radio", { name: "More than half the days" })).toBeChecked()
    },
  },
  section: {
    item: item("section", { title: "About you" }, null),
    value: null,
    expect: () => expect(screen.getByRole("heading", { name: "About you" })).toBeInTheDocument(),
  },
  instructions: {
    item: item("instructions", { body_markdown: "Take your time." }, null),
    value: null,
    expect: () => expect(screen.getByText("Take your time.")).toBeInTheDocument(),
  },
  free_text: {
    item: item("free_text"),
    value: { text: "Sleep through the night." },
    expect: () => {
      expect(screen.getByTestId("forms-free-text")).toHaveTextContent("Sleep through the night.")
      expect(screen.queryByTestId("forms-free-text-counter")).not.toBeInTheDocument()
    },
  },
  single_choice: {
    item: item("single_choice", { options: [{ key: "a", label: "Mornings" }, { key: "b", label: "Evenings" }] }),
    value: { key: "b" },
    expect: () => {
      expect(screen.getByRole("radio", { name: "Evenings" })).toBeChecked()
      expect(screen.getByRole("radio", { name: "Mornings" })).not.toBeChecked()
    },
  },
  multi_choice: {
    item: item("multi_choice", { options: [{ key: "a", label: "Mornings" }, { key: "b", label: "Evenings" }, { key: "c", label: "Weekends" }] }),
    value: { keys: ["a", "c"] },
    expect: () => {
      expect(screen.getByRole("checkbox", { name: "Mornings" })).toBeChecked()
      expect(screen.getByRole("checkbox", { name: "Evenings" })).not.toBeChecked()
      expect(screen.getByRole("checkbox", { name: "Weekends" })).toBeChecked()
    },
  },
  yes_no: {
    item: item("yes_no", { follow_up_label: "Tell us more" }),
    value: { yes: true, follow_up: "Twice last year." },
    expect: () => {
      expect(screen.getByRole("radio", { name: "Yes" })).toBeChecked()
      expect(screen.getByRole("radio", { name: "No" })).not.toBeChecked()
      expect(screen.getByTestId("forms-yes-no-follow-up")).toHaveTextContent("Twice last year.")
    },
  },
  scale: {
    item: item("scale", { min: 1, max: 5, min_label: "Not at all", max_label: "Very much" }),
    value: { value: 4 },
    expect: () => {
      expect(screen.getByRole("radio", { name: "4" })).toBeChecked()
      expect(screen.getByRole("radio", { name: "5" })).not.toBeChecked()
    },
  },
  number: {
    item: item("number", { unit: "hours" }),
    value: { value: 6 },
    expect: () => {
      expect(screen.getByTestId("forms-number")).toHaveValue(6)
      expect(screen.getByText("hours")).toBeInTheDocument()
    },
  },
  date: {
    item: item("date"),
    value: { value: "2026-01-05" },
    expect: () => expect(screen.getByTestId("forms-date")).toHaveValue("2026-01-05"),
  },
  emergency_contact: {
    item: item("emergency_contact"),
    value: { name: "Sam Rivera", relationship: "Sibling", phone: "555-0100" },
    expect: () => {
      expect(screen.getByTestId("forms-contact-name")).toHaveValue("Sam Rivera")
      expect(screen.getByTestId("forms-contact-relationship")).toHaveValue("Sibling")
      expect(screen.getByTestId("forms-contact-phone")).toHaveValue("555-0100")
    },
  },
  consent_document: {
    item: item("consent_document", { document_key: "consent", document_version_id: "docv-1" }, null),
    value: { signed: true, signature_id: "sig-1" },
    expect: async () => {
      expect(await screen.findByText("We keep records private.")).toBeInTheDocument()
      expect(screen.getByRole("heading", { name: "Consent to treatment" })).toBeInTheDocument()
      expect(screen.getByRole("checkbox", { name: "I have read this document and agree to it." })).toBeChecked()
      expect(screen.getByTestId("forms-consent-signed")).toHaveTextContent("Dana Okonkwo")
      expect(screen.getByTestId("forms-consent-signed")).toHaveTextContent(
        new Date("2026-03-14T12:00:00Z").toLocaleString(),
      )
      expect(screen.getByTestId("forms-consent-signer-role")).toHaveTextContent("Signed by the patient")
      expect(screen.queryByTestId("forms-consent-sign")).not.toBeInTheDocument()
      expect(source.loadDocument).toHaveBeenCalledWith("docv-1")
    },
  },
  insurance_card: {
    item: item("insurance_card", { sides: "both", collect_fields: true }),
    value: { documents: ["doc-1"] },
    artifacts: [artifact({ id: "art-1", side: "front", document_id: "doc-front" })],
    expect: async () => {
      expect(screen.getByTestId("forms-upload-front-filename")).toHaveTextContent("front.jpg")
      expect(screen.getByTestId("forms-upload-back")).toHaveTextContent("Not sent.")
      expect(screen.queryByTestId("forms-coverage-fields")).not.toBeInTheDocument()
      await userEvent.click(screen.getByTestId("forms-upload-front-view"))
      expect(source.openFile).toHaveBeenCalledWith("doc-front")
    },
  },
  document_request: {
    item: item("document_request", { blank_form_id: "blank-1" }),
    value: { documents: ["doc-1"] },
    artifacts: [artifact({ id: "art-2", document_id: "doc-letter" })],
    expect: () => {
      expect(screen.getAllByTestId("forms-upload")).toHaveLength(1)
      expect(screen.getByTestId("forms-upload-filename")).toHaveTextContent("back.jpg")
      expect(screen.queryByTestId("forms-blank-form")).not.toBeInTheDocument()
      expect(screen.queryByTestId("forms-upload-choose")).not.toBeInTheDocument()
    },
  },
}

describe("read-only renderers", () => {
  it("has a read-only case for every type the portal draws", () => {
    expect(Object.keys(CASES).sort()).toEqual([...RENDERED_ITEM_TYPES].sort())
  })

  it.each(Object.keys(CASES))("draws %s with its answer, locked, and never writes", async (type) => {
    const shown = CASES[type]
    const { container } = draw(shown.item, shown.value, shown.artifacts)
    await shown.expect()
    expectLocked(container)
    await clickEverything(container)
    for (const route of PATIENT_ROUTES) expect(vi.mocked(patientApi[route])).not.toHaveBeenCalled()
    expect(fetchSpy).not.toHaveBeenCalled()
  })

  it("says a document with no files sent had none", () => {
    draw(item("document_request"), null)
    expect(screen.getByTestId("forms-upload")).toHaveTextContent("Not sent.")
  })

  it("says a consent document nobody signed was not signed", async () => {
    source.signatures = []
    draw(item("consent_document", { document_version_id: "docv-1" }), null)
    expect(await screen.findByTestId("forms-consent-unsigned")).toHaveTextContent("Not signed.")
  })
})
