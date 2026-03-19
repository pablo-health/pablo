// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * QualityRatingWithFeedback Component Tests
 *
 * Tests for conditional feedback collection based on rating threshold.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import {
  QualityRatingWithFeedback,
  type RatingFeedback,
} from "../QualityRatingWithFeedback"

// Mock the config hook
vi.mock("@/lib/config", () => ({
  useConfig: vi.fn(() => ({
    apiUrl: "http://localhost:8000",
    devMode: true,
    dataMode: "mock",
    enableLocalAuth: true,
    firebaseProjectId: "",
    ratingFeedbackRequiredBelow: 3, // Show feedback for ratings 1-2
  })),
}))

describe("QualityRatingWithFeedback", () => {
  const mockOnChange = vi.fn()
  const initialValue: RatingFeedback = {
    rating: null,
    reason: "",
    sections: [],
  }

  beforeEach(() => {
    mockOnChange.mockClear()
  })

  describe("Happy Path", () => {
    it("allows user to rate and provide feedback", () => {
      render(
        <QualityRatingWithFeedback value={initialValue} onChange={mockOnChange} />
      )

      // User selects 2-star rating
      const stars = screen.getAllByRole("button")
      fireEvent.click(stars[1])

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 2,
        reason: "",
        sections: [],
      })
    })

    it("shows feedback UI immediately after low rating is selected", () => {
      const { rerender } = render(
        <QualityRatingWithFeedback value={initialValue} onChange={mockOnChange} />
      )

      // Initially no feedback UI
      expect(
        screen.queryByText("Which sections need improvement?")
      ).not.toBeInTheDocument()

      // Select 2-star rating (below threshold of 3)
      const stars = screen.getAllByRole("button")
      fireEvent.click(stars[1])

      // Rerender with new value
      rerender(
        <QualityRatingWithFeedback
          value={{ rating: 2, reason: "", sections: [] }}
          onChange={mockOnChange}
        />
      )

      // Feedback UI should appear
      expect(
        screen.getByText("Which sections need improvement?")
      ).toBeInTheDocument()
    })
  })

  describe("Conditional Feedback UI", () => {
    it("shows feedback UI when rating is below threshold", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "",
        sections: [],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      expect(
        screen.getByText("Which sections need improvement?")
      ).toBeInTheDocument()
      expect(
        screen.getByText("Additional feedback (optional)")
      ).toBeInTheDocument()
    })

    it("hides feedback UI when rating is at threshold", () => {
      const value: RatingFeedback = {
        rating: 3,
        reason: "",
        sections: [],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      expect(
        screen.queryByText("Which sections need improvement?")
      ).not.toBeInTheDocument()
    })

    it("hides feedback UI when rating is above threshold", () => {
      const value: RatingFeedback = {
        rating: 5,
        reason: "",
        sections: [],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      expect(
        screen.queryByText("Which sections need improvement?")
      ).not.toBeInTheDocument()
    })

    it("hides feedback UI when rating is null", () => {
      render(
        <QualityRatingWithFeedback value={initialValue} onChange={mockOnChange} />
      )

      expect(
        screen.queryByText("Which sections need improvement?")
      ).not.toBeInTheDocument()
    })

    it("clears feedback when rating changes from low to high", () => {
      const { rerender } = render(
        <QualityRatingWithFeedback
          value={{ rating: 2, reason: "Bad quality", sections: ["assessment"] }}
          onChange={mockOnChange}
        />
      )

      // Change rating to 5
      const stars = screen.getAllByRole("button")
      fireEvent.click(stars[4])

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 5,
        reason: "",
        sections: [],
      })
    })
  })

  describe("Section Checkboxes", () => {
    it("renders all four SOAP sections", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "",
        sections: [],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      expect(screen.getByLabelText("Subjective")).toBeInTheDocument()
      expect(screen.getByLabelText("Objective")).toBeInTheDocument()
      expect(screen.getByLabelText("Assessment")).toBeInTheDocument()
      expect(screen.getByLabelText("Plan")).toBeInTheDocument()
    })

    it("adds section when checkbox is checked", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "",
        sections: [],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      const assessmentCheckbox = screen.getByLabelText("Assessment")
      fireEvent.click(assessmentCheckbox)

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 2,
        reason: "",
        sections: ["assessment"],
      })
    })

    it("removes section when checkbox is unchecked", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "",
        sections: ["assessment", "plan"],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      const assessmentCheckbox = screen.getByLabelText("Assessment")
      fireEvent.click(assessmentCheckbox)

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 2,
        reason: "",
        sections: ["plan"],
      })
    })

    it("allows multiple sections to be selected", () => {
      const value: RatingFeedback = {
        rating: 1,
        reason: "",
        sections: ["subjective"],
      }

      const { rerender } = render(
        <QualityRatingWithFeedback value={value} onChange={mockOnChange} />
      )

      // Select assessment
      const assessmentCheckbox = screen.getByLabelText("Assessment")
      fireEvent.click(assessmentCheckbox)

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 1,
        reason: "",
        sections: ["subjective", "assessment"],
      })

      // Rerender with updated sections
      rerender(
        <QualityRatingWithFeedback
          value={{ rating: 1, reason: "", sections: ["subjective", "assessment"] }}
          onChange={mockOnChange}
        />
      )

      // Select plan
      const planCheckbox = screen.getByLabelText("Plan")
      fireEvent.click(planCheckbox)

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 1,
        reason: "",
        sections: ["subjective", "assessment", "plan"],
      })
    })
  })

  describe("Reason Textarea", () => {
    it("updates reason when textarea value changes", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "",
        sections: [],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      const textarea = screen.getByPlaceholderText(
        "What specifically needs improvement?"
      )
      fireEvent.change(textarea, { target: { value: "Assessment was vague" } })

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 2,
        reason: "Assessment was vague",
        sections: [],
      })
    })

    it("preserves existing reason text", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "Initial feedback",
        sections: [],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      const textarea = screen.getByPlaceholderText(
        "What specifically needs improvement?"
      ) as HTMLTextAreaElement

      expect(textarea.value).toBe("Initial feedback")
    })
  })

  describe("Readonly Mode", () => {
    it("hides feedback UI in readonly mode even with low rating", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "Should not show",
        sections: ["assessment"],
      }

      render(
        <QualityRatingWithFeedback
          value={value}
          onChange={mockOnChange}
          readonly
        />
      )

      expect(
        screen.queryByText("Which sections need improvement?")
      ).not.toBeInTheDocument()
    })

    it("does not allow rating changes in readonly mode", () => {
      const value: RatingFeedback = {
        rating: 3,
        reason: "",
        sections: [],
      }

      render(
        <QualityRatingWithFeedback
          value={value}
          onChange={mockOnChange}
          readonly
        />
      )

      const stars = screen.getAllByRole("button")
      fireEvent.click(stars[1])

      expect(mockOnChange).not.toHaveBeenCalled()
    })
  })

  describe("Edge Cases", () => {
    it("applies custom className", () => {
      const { container } = render(
        <QualityRatingWithFeedback
          value={initialValue}
          onChange={mockOnChange}
          className="custom-class"
        />
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("custom-class")
    })

    it("syncs local state with prop changes", () => {
      const { rerender } = render(
        <QualityRatingWithFeedback
          value={{ rating: 2, reason: "Old", sections: [] }}
          onChange={mockOnChange}
        />
      )

      rerender(
        <QualityRatingWithFeedback
          value={{ rating: 3, reason: "New", sections: ["plan"] }}
          onChange={mockOnChange}
        />
      )

      // Should sync to new value
      const textarea = screen.queryByPlaceholderText(
        "What specifically needs improvement?"
      )
      expect(textarea).not.toBeInTheDocument() // rating 3 is at threshold
    })

    it("preserves sections when only reason changes", () => {
      const value: RatingFeedback = {
        rating: 2,
        reason: "Initial",
        sections: ["assessment", "plan"],
      }

      render(<QualityRatingWithFeedback value={value} onChange={mockOnChange} />)

      const textarea = screen.getByPlaceholderText(
        "What specifically needs improvement?"
      )
      fireEvent.change(textarea, { target: { value: "Updated reason" } })

      expect(mockOnChange).toHaveBeenCalledWith({
        rating: 2,
        reason: "Updated reason",
        sections: ["assessment", "plan"],
      })
    })
  })
})
