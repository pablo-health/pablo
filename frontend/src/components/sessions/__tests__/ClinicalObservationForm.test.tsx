// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * ClinicalObservationForm Component Tests
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import {
  ClinicalObservationForm,
  formatClinicalObservation,
  EMPTY_CLINICAL_OBSERVATION,
} from "../ClinicalObservationForm"
import type { ClinicalObservation } from "@/types/sessions"

const fullObservation: ClinicalObservation = {
  appearance: "well-groomed",
  eye_contact: "appropriate",
  psychomotor: "normal",
  psychomotor_notes: "",
  attitude: "",
  non_verbal: "Client maintained relaxed posture",
  affect_observation: "Congruent with stated mood; full range",
}

describe("ClinicalObservationForm", () => {
  describe("Rendering", () => {
    it("renders all form fields", () => {
      render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={vi.fn()}
        />
      )

      expect(screen.getByText("Clinician Observations")).toBeInTheDocument()
      expect(screen.getByText("Appearance")).toBeInTheDocument()
      expect(screen.getByText("Eye Contact")).toBeInTheDocument()
      expect(screen.getByText("Psychomotor Activity")).toBeInTheDocument()
      expect(screen.getByText("Attitude / Behavior")).toBeInTheDocument()
      expect(screen.getByText("Non-verbal")).toBeInTheDocument()
      expect(screen.getByText("Affect Observation")).toBeInTheDocument()
    })

    it("renders with empty default state cleanly", () => {
      const { container } = render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={vi.fn()}
        />
      )

      expect(container.querySelector("fieldset")).toBeInTheDocument()
    })

    it("applies custom className", () => {
      const { container } = render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={vi.fn()}
          className="my-custom-class"
        />
      )

      const fieldset = container.querySelector("fieldset")
      expect(fieldset?.className).toContain("my-custom-class")
    })
  })

  describe("User Interactions", () => {
    it("updates non_verbal textarea via onChange", () => {
      const handleChange = vi.fn()
      render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={handleChange}
        />
      )

      const textarea = screen.getByPlaceholderText(
        "Body language, gestures, posture notes..."
      )
      fireEvent.change(textarea, {
        target: { value: "Relaxed posture" },
      })

      expect(handleChange).toHaveBeenCalledWith({
        ...EMPTY_CLINICAL_OBSERVATION,
        non_verbal: "Relaxed posture",
      })
    })

    it("updates affect_observation textarea via onChange", () => {
      const handleChange = vi.fn()
      render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={handleChange}
        />
      )

      const textarea = screen.getByPlaceholderText(
        "Congruent/incongruent with mood, range, stability..."
      )
      fireEvent.change(textarea, {
        target: { value: "Congruent" },
      })

      expect(handleChange).toHaveBeenCalledWith({
        ...EMPTY_CLINICAL_OBSERVATION,
        affect_observation: "Congruent",
      })
    })

    it("updates psychomotor_notes text input via onChange", () => {
      const handleChange = vi.fn()
      render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={handleChange}
        />
      )

      const input = screen.getByLabelText("Psychomotor notes")
      fireEvent.change(input, {
        target: { value: "Fidgeting" },
      })

      expect(handleChange).toHaveBeenCalledWith({
        ...EMPTY_CLINICAL_OBSERVATION,
        psychomotor_notes: "Fidgeting",
      })
    })
  })

  describe("Read-only Mode", () => {
    it("renders values as text, not form inputs", () => {
      render(
        <ClinicalObservationForm
          value={fullObservation}
          onChange={vi.fn()}
          readonly
        />
      )

      expect(screen.getByText("Clinician Observations")).toBeInTheDocument()
      expect(screen.getByText("well-groomed")).toBeInTheDocument()
      expect(screen.getByText("appropriate")).toBeInTheDocument()
      expect(screen.getByText("Normal")).toBeInTheDocument()
      expect(
        screen.getByText("Client maintained relaxed posture")
      ).toBeInTheDocument()
      expect(
        screen.getByText("Congruent with stated mood; full range")
      ).toBeInTheDocument()

      // No inputs or textareas
      expect(screen.queryByRole("combobox")).not.toBeInTheDocument()
      expect(screen.queryByRole("textbox")).not.toBeInTheDocument()
    })

    it("hides empty fields in read-only mode", () => {
      render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={vi.fn()}
          readonly
        />
      )

      expect(screen.getByText("Clinician Observations")).toBeInTheDocument()
      // Labels should not appear for empty values
      expect(screen.queryByText("Appearance")).not.toBeInTheDocument()
      expect(screen.queryByText("Eye Contact")).not.toBeInTheDocument()
    })

    it("combines psychomotor and notes in read-only mode", () => {
      const obs: ClinicalObservation = {
        ...EMPTY_CLINICAL_OBSERVATION,
        psychomotor: "agitation",
        psychomotor_notes: "Leg bouncing throughout session",
      }
      render(
        <ClinicalObservationForm value={obs} onChange={vi.fn()} readonly />
      )

      expect(
        screen.getByText("Agitation — Leg bouncing throughout session")
      ).toBeInTheDocument()
    })

    it("shows attitude in read-only mode when set", () => {
      const obs: ClinicalObservation = {
        ...EMPTY_CLINICAL_OBSERVATION,
        attitude: "cooperative",
      }
      render(
        <ClinicalObservationForm value={obs} onChange={vi.fn()} readonly />
      )

      expect(screen.getByText("Attitude / Behavior")).toBeInTheDocument()
      expect(screen.getByText("Cooperative")).toBeInTheDocument()
    })
  })

  describe("Accessibility", () => {
    it("has labels associated with textareas", () => {
      render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={vi.fn()}
        />
      )

      expect(screen.getByLabelText("Non-verbal")).toBeInTheDocument()
      expect(screen.getByLabelText("Affect Observation")).toBeInTheDocument()
    })

    it("has aria-labels for additional text inputs", () => {
      render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={vi.fn()}
        />
      )

      expect(screen.getByLabelText("Psychomotor notes")).toBeInTheDocument()
    })

    it("uses a fieldset with legend for form grouping", () => {
      const { container } = render(
        <ClinicalObservationForm
          value={EMPTY_CLINICAL_OBSERVATION}
          onChange={vi.fn()}
        />
      )

      const fieldset = container.querySelector("fieldset")
      expect(fieldset).toBeInTheDocument()

      const legend = container.querySelector("legend")
      expect(legend).toHaveTextContent("Clinician Observations")
    })
  })
})

describe("formatClinicalObservation", () => {
  it("produces correct formatted output with all fields", () => {
    const result = formatClinicalObservation(fullObservation)

    expect(result).toContain("**Clinician Observations:**")
    expect(result).toContain("**Appearance:** Well-groomed")
    expect(result).toContain("**Eye Contact:** Appropriate")
    expect(result).toContain("**Psychomotor Activity:** Normal")
    expect(result).toContain("**Non-verbal:** Client maintained relaxed posture")
    expect(result).toContain(
      "**Affect Observation:** Congruent with stated mood; full range"
    )
  })

  it("omits empty fields from output", () => {
    const result = formatClinicalObservation(EMPTY_CLINICAL_OBSERVATION)

    expect(result).toBe("**Clinician Observations:**")
    expect(result).not.toContain("Appearance")
    expect(result).not.toContain("Eye Contact")
  })

  it("combines psychomotor and notes when both present", () => {
    const obs: ClinicalObservation = {
      ...EMPTY_CLINICAL_OBSERVATION,
      psychomotor: "agitation",
      psychomotor_notes: "Fidgeting with hands",
    }
    const result = formatClinicalObservation(obs)

    expect(result).toContain(
      "**Psychomotor Activity:** Agitation — Fidgeting with hands"
    )
  })

  it("renders psychomotor without notes when notes are empty", () => {
    const obs: ClinicalObservation = {
      ...EMPTY_CLINICAL_OBSERVATION,
      psychomotor: "normal",
    }
    const result = formatClinicalObservation(obs)

    expect(result).toContain("**Psychomotor Activity:** Normal")
    expect(result).not.toContain("—")
  })

  it("handles partial observations", () => {
    const obs: ClinicalObservation = {
      ...EMPTY_CLINICAL_OBSERVATION,
      appearance: "well-groomed",
      non_verbal: "Open body language",
    }
    const result = formatClinicalObservation(obs)

    expect(result).toContain("**Appearance:** Well-groomed")
    expect(result).toContain("**Non-verbal:** Open body language")
    expect(result).not.toContain("Eye Contact")
    expect(result).not.toContain("Psychomotor")
    expect(result).not.toContain("Affect Observation")
  })

  it("includes attitude in formatted output when set", () => {
    const obs: ClinicalObservation = {
      ...EMPTY_CLINICAL_OBSERVATION,
      attitude: "cooperative",
    }
    const result = formatClinicalObservation(obs)

    expect(result).toContain("**Attitude:** Cooperative")
  })

  it("omits attitude from output when empty", () => {
    const result = formatClinicalObservation(EMPTY_CLINICAL_OBSERVATION)

    expect(result).not.toContain("Attitude")
  })
})
