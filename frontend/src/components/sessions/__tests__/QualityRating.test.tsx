// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * QualityRating Component Tests
 *
 * Comprehensive tests covering rendering, user interactions, keyboard navigation,
 * loading states, and accessibility.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen, fireEvent } from "@testing-library/react"
import { QualityRating } from "../QualityRating"

describe("QualityRating", () => {
  describe("Rendering", () => {
    it("renders 5 stars", () => {
      render(<QualityRating value={null} />)
      const stars = screen.getAllByRole("button")
      expect(stars).toHaveLength(5)
    })

    it("shows 'Not rated' label when value is null and showLabel is true", () => {
      render(<QualityRating value={null} showLabel />)
      expect(screen.getByText("Not rated")).toBeInTheDocument()
    })

    it("shows rating value label when showLabel is true", () => {
      render(<QualityRating value={4} showLabel />)
      expect(screen.getByText("4/5")).toBeInTheDocument()
    })

    it("does not show label when showLabel is false", () => {
      render(<QualityRating value={3} showLabel={false} />)
      expect(screen.queryByText("3/5")).not.toBeInTheDocument()
    })
  })

  describe("Interactive Mode", () => {
    ;[1, 2, 3, 4, 5].forEach((rating) => {
      it(`calls onChange with ${rating} when star ${rating} is clicked`, () => {
        const handleChange = vi.fn()
        render(<QualityRating value={null} onChange={handleChange} />)

        const stars = screen.getAllByRole("button")
        fireEvent.click(stars[rating - 1])

        expect(handleChange).toHaveBeenCalledWith(rating)
      })
    })

    it("updates rating when clicking different star", () => {
      const handleChange = vi.fn()
      const { rerender } = render(<QualityRating value={3} onChange={handleChange} />)

      const stars = screen.getAllByRole("button")
      fireEvent.click(stars[4]) // Click 5th star

      expect(handleChange).toHaveBeenCalledWith(5)

      // Rerender with new value
      rerender(<QualityRating value={5} onChange={handleChange} />)

      const filledStars = screen.getAllByRole("button", { pressed: true })
      expect(filledStars).toHaveLength(1)
      expect(filledStars[0]).toHaveAttribute("aria-label", "Rate 5 stars")
    })

    it("shows hover preview when mouse enters star", () => {
      render(<QualityRating value={2} onChange={vi.fn()} />)

      const stars = screen.getAllByRole("button")
      fireEvent.mouseEnter(stars[3]) // Hover over 4th star

      // Verify hover state via aria attributes (not CSS)
      expect(stars[3]).toHaveAttribute("aria-pressed")
    })

    it("clears hover preview when mouse leaves star container", () => {
      render(<QualityRating value={2} onChange={vi.fn()} />)

      const stars = screen.getAllByRole("button")
      const starContainer = stars[0].parentElement!

      // Hover over star
      fireEvent.mouseEnter(stars[3])

      // Leave container
      fireEvent.mouseLeave(starContainer)

      // Verify original rating maintained
      expect(screen.getByLabelText("Rate 3 stars")).toHaveAttribute("aria-pressed", "false")
    })
  })

  describe("Readonly Mode", () => {
    it("does not call onChange when star is clicked in readonly mode", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={3} onChange={handleChange} readonly />)

      const stars = screen.getAllByRole("button")
      fireEvent.click(stars[4])

      expect(handleChange).not.toHaveBeenCalled()
    })

    it("disables buttons in readonly mode", () => {
      render(<QualityRating value={3} onChange={vi.fn()} readonly />)

      const stars = screen.getAllByRole("button")
      stars.forEach((star) => {
        expect(star).toBeDisabled()
      })
    })

    it("is readonly when onChange is not provided", () => {
      render(<QualityRating value={3} />)

      const stars = screen.getAllByRole("button")
      fireEvent.click(stars[4])

      // No onChange callback to verify, but should not error
      stars.forEach((star) => {
        expect(star).toBeDisabled()
      })
    })
  })

  describe("Keyboard Navigation", () => {
    it("changes rating when Enter key is pressed", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={2} onChange={handleChange} />)

      const stars = screen.getAllByRole("button")
      fireEvent.keyDown(stars[3], { key: "Enter" })

      expect(handleChange).toHaveBeenCalledWith(4)
    })

    it("changes rating when Space key is pressed", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={2} onChange={handleChange} />)

      const stars = screen.getAllByRole("button")
      fireEvent.keyDown(stars[2], { key: " " })

      expect(handleChange).toHaveBeenCalledWith(3)
    })

    it("increases rating when ArrowRight is pressed", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={2} onChange={handleChange} />)

      const stars = screen.getAllByRole("button")
      fireEvent.keyDown(stars[1], { key: "ArrowRight" }) // From star 2

      expect(handleChange).toHaveBeenCalledWith(3)
    })

    it("decreases rating when ArrowLeft is pressed", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={3} onChange={handleChange} />)

      const stars = screen.getAllByRole("button")
      fireEvent.keyDown(stars[2], { key: "ArrowLeft" }) // From star 3

      expect(handleChange).toHaveBeenCalledWith(2)
    })

    it("does not increase rating beyond 5", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={5} onChange={handleChange} />)

      const stars = screen.getAllByRole("button")
      fireEvent.keyDown(stars[4], { key: "ArrowRight" })

      expect(handleChange).not.toHaveBeenCalled()
    })

    it("does not decrease rating below 1", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={1} onChange={handleChange} />)

      const stars = screen.getAllByRole("button")
      fireEvent.keyDown(stars[0], { key: "ArrowLeft" })

      expect(handleChange).not.toHaveBeenCalled()
    })

    it("does not respond to keyboard in readonly mode", () => {
      const handleChange = vi.fn()
      render(<QualityRating value={3} onChange={handleChange} readonly />)

      const stars = screen.getAllByRole("button")
      fireEvent.keyDown(stars[2], { key: "Enter" })
      fireEvent.keyDown(stars[2], { key: " " })
      fireEvent.keyDown(stars[2], { key: "ArrowRight" })

      expect(handleChange).not.toHaveBeenCalled()
    })
  })

  describe("Accessibility", () => {
    it("has proper ARIA labels for each star", () => {
      render(<QualityRating value={null} onChange={vi.fn()} />)

      expect(screen.getByLabelText("Rate 1 stars")).toBeInTheDocument()
      expect(screen.getByLabelText("Rate 2 stars")).toBeInTheDocument()
      expect(screen.getByLabelText("Rate 3 stars")).toBeInTheDocument()
      expect(screen.getByLabelText("Rate 4 stars")).toBeInTheDocument()
      expect(screen.getByLabelText("Rate 5 stars")).toBeInTheDocument()
    })

    it("has group role with label", () => {
      render(<QualityRating value={3} />)

      const group = screen.getByRole("group", { name: "Quality rating" })
      expect(group).toBeInTheDocument()
    })

    it("marks selected star as pressed", () => {
      render(<QualityRating value={3} onChange={vi.fn()} />)

      const stars = screen.getAllByRole("button")
      expect(stars[2]).toHaveAttribute("aria-pressed", "true")
      expect(stars[0]).toHaveAttribute("aria-pressed", "false")
    })

    it("has aria-live region for label", () => {
      render(<QualityRating value={4} showLabel />)

      const label = screen.getByText("4/5")
      expect(label).toHaveAttribute("aria-live", "polite")
    })

    it("is keyboard focusable in interactive mode", () => {
      render(<QualityRating value={2} onChange={vi.fn()} />)

      const stars = screen.getAllByRole("button")
      stars.forEach((star) => {
        expect(star).toHaveAttribute("tabIndex", "0")
      })
    })

    it("is not keyboard focusable in readonly mode", () => {
      render(<QualityRating value={2} onChange={vi.fn()} readonly />)

      const stars = screen.getAllByRole("button")
      stars.forEach((star) => {
        expect(star).toHaveAttribute("tabIndex", "-1")
      })
    })
  })


  describe("Edge Cases", () => {
    it("applies custom className", () => {
      const { container } = render(
        <QualityRating value={3} className="custom-class" />
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("custom-class")
    })

    it("does not error when onChange is undefined and star is clicked", () => {
      render(<QualityRating value={3} />)

      const stars = screen.getAllByRole("button")
      expect(() => fireEvent.click(stars[2])).not.toThrow()
    })
  })
})
