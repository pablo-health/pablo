// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Practice API Function Tests
 *
 * Tests that audio-retention API functions call the client correctly.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import * as client from "../client"
import {
  AUDIO_RETENTION_DEFAULT_DAYS,
  AUDIO_RETENTION_MAX_DAYS,
  AUDIO_RETENTION_MIN_DAYS,
  updateAudioRetention,
} from "../practices"

vi.mock("../client")

describe("Practice API constants", () => {
  it("matches the backend range and default", () => {
    expect(AUDIO_RETENTION_MIN_DAYS).toBe(30)
    expect(AUDIO_RETENTION_MAX_DAYS).toBe(2555)
    expect(AUDIO_RETENTION_DEFAULT_DAYS).toBe(365)
  })
})

describe("updateAudioRetention", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("calls put with the correct endpoint and payload", async () => {
    vi.mocked(client.put).mockResolvedValue({
      practice_id: "prac_123",
      audio_retention_days: 400,
    })

    const result = await updateAudioRetention("prac_123", 400)

    expect(client.put).toHaveBeenCalledWith(
      "/api/saas/practices/prac_123/audio-retention",
      { days: 400 },
      undefined,
    )
    expect(result).toEqual({
      practice_id: "prac_123",
      audio_retention_days: 400,
    })
  })

  it("forwards the auth token when provided", async () => {
    vi.mocked(client.put).mockResolvedValue({
      practice_id: "prac_42",
      audio_retention_days: 365,
    })

    await updateAudioRetention("prac_42", 365, "tok-abc")

    expect(client.put).toHaveBeenCalledWith(
      "/api/saas/practices/prac_42/audio-retention",
      { days: 365 },
      "tok-abc",
    )
  })

  it("propagates client errors", async () => {
    const err = new Error("HTTP 422")
    vi.mocked(client.put).mockRejectedValue(err)

    await expect(updateAudioRetention("prac_x", 10)).rejects.toBe(err)
  })
})
