// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A preferences save must carry only the keys it changes. A full copy read
 * before somebody else's save puts their keys back as they were, which is how
 * two saves close together used to erase each other.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import * as client from "../client"
import { updatePreferences } from "../users"

vi.mock("../client")

describe("updatePreferences", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("sends a PATCH carrying exactly the changed keys", async () => {
    vi.mocked(client.patch).mockResolvedValue({ theme: "dark" })

    await updatePreferences({ theme: "dark", people_term: null }, "tok-abc")

    expect(client.patch).toHaveBeenCalledWith(
      "/api/users/me/preferences",
      { theme: "dark", people_term: null },
      "tok-abc",
    )
    expect(client.put).not.toHaveBeenCalled()
  })
})
