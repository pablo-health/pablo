/**
 * What the card accepts as a pasted Zoom room.
 *
 * `fixtures/zoom_room_url_cases.json` is a copy of
 * `backend/tests/fixtures/zoom_room_url_cases.json`, which the server's check
 * runs too, and `backend/tests/test_zoom_room_url_fixture_sync.py` pins the
 * two identical. Edit the backend one and copy it over. A card that accepts
 * what the server refuses leaves the clinician on a "Try again" that never
 * works; a card that refuses what the server accepts hides a working room.
 */
import { describe, expect, it } from "vitest"
import { isZoomRoomUrl } from "../telehealth"
import cases from "./fixtures/zoom_room_url_cases.json"

describe("isZoomRoomUrl", () => {
  it.each(cases.accept)("accepts %j", (url) => {
    expect(isZoomRoomUrl(url)).toBe(true)
  })

  it.each(cases.refuse)("refuses %j", (url) => {
    expect(isZoomRoomUrl(url)).toBe(false)
  })

  it("every case a browser would open resolves to zoom.us or a subdomain", () => {
    for (const url of cases.accept) {
      const host = new URL(url.trim()).hostname.replace(/\.$/, "")
      expect(host === "zoom.us" || host.endsWith(".zoom.us")).toBe(true)
    }
  })
})
