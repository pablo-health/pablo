// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The expectation notice.
 *
 * These are exact-string assertions on purpose. The strings are a
 * reviewed artifact; a test that matched them loosely would let them
 * drift without anyone reviewing the drift.
 */

import { describe, expect, it } from "vitest"
import { render, screen } from "@testing-library/react"
import {
  CRISIS_LINE,
  DEFAULT_RESPONSE_TIME,
  ExpectationNotice,
  NOT_FOR_EMERGENCIES,
} from "../ExpectationNotice"

describe("ExpectationNotice", () => {
  it("pins the shipped strings", () => {
    expect(NOT_FOR_EMERGENCIES).toBe("Messaging isn't for emergencies.")
    expect(CRISIS_LINE).toBe(
      "If you need help right away, call 911, or call or text 988 (Suicide & Crisis Lifeline).",
    )
    expect(DEFAULT_RESPONSE_TIME).toBe(
      "Your therapist typically replies within a few business days.",
    )
  })

  it("renders the default reply time when nothing is configured", () => {
    render(<ExpectationNotice />)

    expect(screen.getByText(NOT_FOR_EMERGENCIES)).toBeTruthy()
    expect(screen.getByText(CRISIS_LINE)).toBeTruthy()
    expect(screen.getByTestId("portal-messaging-response-time").textContent).toBe(
      DEFAULT_RESPONSE_TIME,
    )
  })

  it.each([undefined, null, "", "   "])(
    "falls back to the default for %p",
    (slaText) => {
      render(<ExpectationNotice slaText={slaText} />)

      expect(screen.getByTestId("portal-messaging-response-time").textContent).toBe(
        DEFAULT_RESPONSE_TIME,
      )
    },
  )

  it("renders the configured reply time when the practice has set one", () => {
    render(<ExpectationNotice slaText="We reply on Tuesdays and Thursdays." />)

    expect(screen.getByTestId("portal-messaging-response-time").textContent).toBe(
      "We reply on Tuesdays and Thursdays.",
    )
    // Configuring reply times never buys silence on the rest.
    expect(screen.getByText(NOT_FOR_EMERGENCIES)).toBeTruthy()
    expect(screen.getByText(CRISIS_LINE)).toBeTruthy()
  })

  it("renders 988 under every prop it accepts", () => {
    for (const slaText of [undefined, null, "", "Same day, always."]) {
      const { unmount } = render(<ExpectationNotice slaText={slaText} />)
      expect(
        screen.getByTestId("portal-messaging-expectation-notice").textContent,
      ).toContain("988")
      unmount()
    }
  })
})
