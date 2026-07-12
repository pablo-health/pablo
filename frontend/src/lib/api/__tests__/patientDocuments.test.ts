// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * uploadFileToStorage executes the backend-provided UploadTarget recipe
 * verbatim: bare PUT with the target's headers (GCS signed URL), or
 * multipart form POST with the target's policy fields (S3 presigned
 * POST). Both shapes hit fetch directly (no apiClient).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { uploadFileToStorage } from "../patientDocuments"

const file = new File(["%PDF-1.7 body"], "report.pdf", {
  type: "application/pdf",
})

describe("uploadFileToStorage", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("executes a PUT target with the provider-signed headers (GCS)", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 200 }))

    await uploadFileToStorage(
      {
        url: "https://storage.example/signed",
        method: "PUT",
        headers: {
          "Content-Type": "application/pdf",
          "x-goog-content-length-range": "0,100",
        },
        fields: {},
      },
      file,
    )

    expect(fetch).toHaveBeenCalledWith("https://storage.example/signed", {
      method: "PUT",
      headers: {
        "Content-Type": "application/pdf",
        "x-goog-content-length-range": "0,100",
      },
      body: file,
    })
  })

  it("executes a POST target as a form with policy fields first, file last (S3)", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))

    await uploadFileToStorage(
      {
        url: "https://pablo-docs.s3.example",
        method: "POST",
        headers: {},
        fields: { key: "tenant-A/chart/doc-1", policy: "b64policy" },
      },
      file,
    )

    const [url, init] = vi.mocked(fetch).mock.calls[0]
    expect(url).toBe("https://pablo-docs.s3.example")
    expect(init?.method).toBe("POST")
    // No explicit headers — the browser sets the multipart boundary.
    expect(init?.headers).toBeUndefined()
    const form = init?.body as FormData
    const entries = [...form.entries()]
    expect(entries.map(([name]) => name)).toEqual(["key", "policy", "file"])
    expect(form.get("key")).toBe("tenant-A/chart/doc-1")
    expect(form.get("file")).toBe(file)
  })

  it("throws with status and body detail on a non-2xx response", async () => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("EntityTooLarge", { status: 400 }),
    )

    await expect(
      uploadFileToStorage(
        {
          url: "https://pablo-docs.s3.example",
          method: "POST",
          headers: {},
          fields: {},
        },
        file,
      ),
    ).rejects.toThrow("Storage upload failed (400): EntityTooLarge")
  })
})
