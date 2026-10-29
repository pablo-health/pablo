// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { describe, expect, it } from "vitest"
import { isTelehealth } from "../telehealth"

const office = { provider: null, video_link: null, place_of_service: null }

describe("isTelehealth", () => {
  it.each([
    ["a video service", { provider: "doxy_me" }],
    ["a video link", { video_link: "https://video.example/room" }],
    ["telehealth in the home", { place_of_service: "10" }],
    ["telehealth elsewhere", { place_of_service: "02" }],
  ])("is true for %s", (_label, where) => {
    expect(isTelehealth({ ...office, ...where })).toBe(true)
  })

  it.each([
    ["nothing set", {}],
    ["the office", { place_of_service: "11" }],
  ])("is false for %s", (_label, where) => {
    expect(isTelehealth({ ...office, ...where })).toBe(false)
  })
})
