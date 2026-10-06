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
  AUDIO_RETENTION_ON_SIGNING,
  getAudioRetention,
  updateAudioRetention,
} from "../practices"

vi.mock("../client")

const PATH = "/api/users/me/practice/audio-retention"

describe("Practice API constants", () => {
  it("matches the backend range and default", () => {
    expect(AUDIO_RETENTION_ON_SIGNING).toBe(0)
    expect(AUDIO_RETENTION_MIN_DAYS).toBe(1)
    expect(AUDIO_RETENTION_MAX_DAYS).toBe(2555)
    expect(AUDIO_RETENTION_DEFAULT_DAYS).toBe(365)
  })
})

describe("getAudioRetention", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("reads the caller's own practice", async () => {
    vi.mocked(client.get).mockResolvedValue({ practice_id: "prac_1", audio_retention_days: 0 })

    const result = await getAudioRetention("tok-abc")

    expect(client.get).toHaveBeenCalledWith(PATH, "tok-abc")
    expect(result.audio_retention_days).toBe(0)
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

    const result = await updateAudioRetention(400)

    expect(client.put).toHaveBeenCalledWith(PATH, { audio_retention_days: 400 }, undefined)
    expect(result).toEqual({
      practice_id: "prac_123",
      audio_retention_days: 400,
    })
  })

  it("sends 0 for delete-on-signing and forwards the auth token", async () => {
    vi.mocked(client.put).mockResolvedValue({
      practice_id: "prac_42",
      audio_retention_days: 0,
    })

    await updateAudioRetention(AUDIO_RETENTION_ON_SIGNING, "tok-abc")

    expect(client.put).toHaveBeenCalledWith(PATH, { audio_retention_days: 0 }, "tok-abc")
  })

  it("propagates client errors", async () => {
    const err = new Error("HTTP 422")
    vi.mocked(client.put).mockRejectedValue(err)

    await expect(updateAudioRetention(10)).rejects.toBe(err)
  })
})
