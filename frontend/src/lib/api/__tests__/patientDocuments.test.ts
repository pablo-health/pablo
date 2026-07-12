// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * uploadFileToSignedUrl dispatches on the provider's upload method:
 * bare PUT for GCS signed URLs, multipart form POST for S3 presigned
 * POST policies. Both shapes hit fetch directly (no apiClient).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { uploadFileToSignedUrl } from "../patientDocuments"

const file = new File(["%PDF-1.7 body"], "report.pdf", {
  type: "application/pdf",
})

describe("uploadFileToSignedUrl", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn())
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it("defaults to a bare PUT with the signed size-range header (GCS)", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 200 }))

    await uploadFileToSignedUrl(
      "https://storage.example/signed",
      file,
      100,
      "application/pdf",
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

  it("sends a form POST with policy fields first and file last (S3)", async () => {
    vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 204 }))

    await uploadFileToSignedUrl(
      "https://pablo-docs.s3.example",
      file,
      100,
      "application/pdf",
      "POST",
      { key: "tenant-A/chart/doc-1", policy: "b64policy" },
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
      uploadFileToSignedUrl(
        "https://pablo-docs.s3.example",
        file,
        100,
        "application/pdf",
        "POST",
        {},
      ),
    ).rejects.toThrow("Storage upload failed (400): EntityTooLarge")
  })
})
