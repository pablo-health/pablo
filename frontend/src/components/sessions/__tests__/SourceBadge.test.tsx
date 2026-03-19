// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * SourceBadge Component Tests
 *
 * Tests for the verified/unverified visual indicators used on SOAP claims,
 * including confidence-level gradations.
 */

import { describe, it, expect, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import { SourceBadge, SourceHighlight } from "../SourceBadge"

vi.mock("@/lib/config", () => ({
  useConfig: () => ({ showVerificationBadges: true }),
}))

describe("SourceBadge", () => {
  describe("Binary Fallback (no confidenceLevel)", () => {
    it("shows 'sourced' for verified claims", () => {
      render(<SourceBadge sourceSegmentIds={[1, 2]} />)

      expect(screen.getByText("sourced")).toBeInTheDocument()
      expect(screen.queryByText("(unverified)")).not.toBeInTheDocument()
    })

    it("shows '(unverified)' for claims with no source references", () => {
      render(<SourceBadge sourceSegmentIds={[]} />)

      expect(screen.getByText("(unverified)")).toBeInTheDocument()
      expect(screen.queryByText("sourced")).not.toBeInTheDocument()
    })

    it("treats a single source segment as verified", () => {
      render(<SourceBadge sourceSegmentIds={[5]} />)

      expect(screen.getByText("sourced")).toBeInTheDocument()
    })

    it("falls back to binary behavior when confidenceLevel is empty string", () => {
      render(<SourceBadge sourceSegmentIds={[1]} confidenceLevel="" />)

      expect(screen.getByText("sourced")).toBeInTheDocument()
    })

    it("falls back to unverified when confidenceLevel is empty and no sources", () => {
      render(<SourceBadge sourceSegmentIds={[]} confidenceLevel="" />)

      expect(screen.getByText("(unverified)")).toBeInTheDocument()
    })
  })

  describe("Confidence Levels", () => {
    it("shows 'verified' with green styling for high confidence", () => {
      const { container } = render(
        <SourceBadge sourceSegmentIds={[1]} confidenceLevel="high" confidenceScore={0.92} />
      )

      expect(screen.getByText("verified")).toBeInTheDocument()
      const badge = container.firstChild as HTMLElement
      expect(badge.className).toContain("text-green-600")
    })

    it("shows 'sourced' with neutral styling for medium confidence", () => {
      const { container } = render(
        <SourceBadge sourceSegmentIds={[1]} confidenceLevel="medium" confidenceScore={0.65} />
      )

      expect(screen.getByText("sourced")).toBeInTheDocument()
      const badge = container.firstChild as HTMLElement
      expect(badge.className).toContain("text-neutral-400")
    })

    it("shows 'low confidence' with orange styling for low confidence", () => {
      const { container } = render(
        <SourceBadge sourceSegmentIds={[1]} confidenceLevel="low" confidenceScore={0.3} />
      )

      expect(screen.getByText("low confidence")).toBeInTheDocument()
      const badge = container.firstChild as HTMLElement
      expect(badge.className).toContain("text-orange-600")
    })

    it("shows '(unverified)' with yellow styling for unverified confidence", () => {
      const { container } = render(
        <SourceBadge sourceSegmentIds={[]} confidenceLevel="unverified" confidenceScore={0.0} />
      )

      expect(screen.getByText("(unverified)")).toBeInTheDocument()
      const badge = container.firstChild as HTMLElement
      expect(badge.className).toContain("text-yellow-700")
    })

    it("includes confidence percentage in title attribute", () => {
      render(
        <SourceBadge sourceSegmentIds={[1]} confidenceLevel="high" confidenceScore={0.87} />
      )

      const badge = screen.getByText("verified")
      expect(badge.getAttribute("title")).toBe("Confidence: 87%")
    })

    it("omits title when confidenceScore is not provided", () => {
      render(
        <SourceBadge sourceSegmentIds={[1]} confidenceLevel="high" />
      )

      const badge = screen.getByText("verified")
      expect(badge.getAttribute("title")).toBeNull()
    })
  })
})

describe("SourceHighlight", () => {
  describe("Binary Fallback (no confidenceLevel)", () => {
    it("applies yellow highlight styling for unverified claims", () => {
      const { container } = render(
        <SourceHighlight sourceSegmentIds={[]}>
          <span>Claim text</span>
        </SourceHighlight>
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("border-yellow-400")
      expect(wrapper.className).toContain("bg-yellow-50/50")
    })

    it("does not apply yellow highlight for verified claims", () => {
      const { container } = render(
        <SourceHighlight sourceSegmentIds={[1, 2]}>
          <span>Claim text</span>
        </SourceHighlight>
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).not.toContain("border-yellow-400")
      expect(wrapper.className).not.toContain("bg-yellow-50/50")
    })

    it("renders children in both verified and unverified states", () => {
      const { rerender } = render(
        <SourceHighlight sourceSegmentIds={[]}>
          <span>Content</span>
        </SourceHighlight>
      )
      expect(screen.getByText("Content")).toBeInTheDocument()

      rerender(
        <SourceHighlight sourceSegmentIds={[1]}>
          <span>Content</span>
        </SourceHighlight>
      )
      expect(screen.getByText("Content")).toBeInTheDocument()
    })
  })

  describe("Confidence-Based Borders", () => {
    it("applies green border for high confidence", () => {
      const { container } = render(
        <SourceHighlight sourceSegmentIds={[1]} confidenceLevel="high">
          <span>High confidence claim</span>
        </SourceHighlight>
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("border-green-400")
    })

    it("does not apply special border for medium confidence", () => {
      const { container } = render(
        <SourceHighlight sourceSegmentIds={[1]} confidenceLevel="medium">
          <span>Medium confidence claim</span>
        </SourceHighlight>
      )

      // Medium is not in HIGHLIGHT_BORDERS — falls through to binary fallback
      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).not.toContain("border-green-400")
      expect(wrapper.className).not.toContain("border-orange-400")
      expect(wrapper.className).not.toContain("border-yellow-400")
    })

    it("applies orange border for low confidence", () => {
      const { container } = render(
        <SourceHighlight sourceSegmentIds={[1]} confidenceLevel="low">
          <span>Low confidence claim</span>
        </SourceHighlight>
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("border-orange-400")
    })

    it("applies yellow border for unverified confidence", () => {
      const { container } = render(
        <SourceHighlight sourceSegmentIds={[]} confidenceLevel="unverified">
          <span>Unverified claim</span>
        </SourceHighlight>
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("border-yellow-400")
    })

    it("falls back to binary behavior when confidenceLevel is empty", () => {
      const { container } = render(
        <SourceHighlight sourceSegmentIds={[]} confidenceLevel="">
          <span>Fallback claim</span>
        </SourceHighlight>
      )

      const wrapper = container.firstChild as HTMLElement
      expect(wrapper.className).toContain("border-yellow-400")
    })
  })
})
