// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * uploadFileToStorage executes the backend-provided UploadTarget recipe
 * verbatim: bare PUT with the target's headers (GCS signed URL), or
 * multipart form POST with the target's policy fields (S3 presigned
 * POST). Both shapes hit fetch directly (no apiClient).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import * as client from "../client"
import {
  finalizePatientDocumentUpload,
  getPatientDocument,
  uploadFileToStorage,
} from "../patientDocuments"
import type { PatientDocumentResponse } from "@/types/patientDocuments"

vi.mock("../client")

const file = new File(["%PDF-1.7 body"], "report.pdf", {
  type: "application/pdf",
})

const pendingDocument: PatientDocumentResponse = {
  id: "doc-1",
  patient_id: "patient-1",
  filename: "report.pdf",
  mime_type: "application/pdf",
  size_bytes: 2048,
  created_at: "2026-08-09T00:00:00Z",
  finalized_at: "2026-08-09T00:00:01Z",
  category: "chart",
  extracted_text: null,
  extraction_status: "pending",
  text_extraction_failed: false,
}

describe("finalizePatientDocumentUpload", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("returns the pending document from a 202 finalize response", async () => {
    vi.mocked(client.post).mockResolvedValue(pendingDocument)

    const result = await finalizePatientDocumentUpload("doc-1")

    expect(client.post).toHaveBeenCalledWith(
      "/api/documents/doc-1/finalize",
      {},
      undefined,
    )
    expect(result.extraction_status).toBe("pending")
  })
})

describe("getPatientDocument", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("passes extraction_status through untouched", async () => {
    vi.mocked(client.get).mockResolvedValue({
      ...pendingDocument,
      extraction_status: "failed",
      text_extraction_failed: true,
    })

    const result = await getPatientDocument("doc-1")

    expect(result.extraction_status).toBe("failed")
    expect(result.text_extraction_failed).toBe(true)
  })
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
