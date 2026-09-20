// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The patient's own documents client.
 *
 * Pins the same three things its messaging sibling does — every request
 * carries the patient session token, no request carries a patient id, and
 * a failure never quotes the payload — plus the ordering that makes an
 * attachment possible: init, then the browser's own upload to storage,
 * then finalize. A send names documents that already exist, so an upload
 * that stopped after step two leaves a row no message can reach.
 */

import { beforeEach, describe, expect, it, vi, afterEach } from "vitest"
import {
  PatientDocumentsError,
  finalizeOwnDocumentUpload,
  getOwnDocumentDownloadUrl,
  initOwnDocumentUpload,
  uploadOwnDocument,
} from "../patientPortalDocuments"

vi.mock("@/lib/api/client", () => ({
  buildApiUrl: (endpoint: string) => `http://test${endpoint}`,
}))

const uploadToStorage = vi.fn()
vi.mock("@/lib/api/patientDocuments", () => ({
  uploadFileToStorage: (...args: unknown[]) => uploadToStorage(...args),
}))

const TOKEN = "session-token"

function okResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response
}

const fetchMock = vi.fn()

beforeEach(() => {
  fetchMock.mockReset()
  uploadToStorage.mockReset()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function lastCall(): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1)
  return [call![0] as string, (call![1] ?? {}) as RequestInit]
}

function authHeader(init: RequestInit): string | undefined {
  return (init.headers as Record<string, string> | undefined)?.Authorization
}

const UPLOAD_TARGET = {
  url: "http://storage/put",
  method: "PUT" as const,
  headers: {},
  fields: {},
}

describe("patientPortalDocuments client", () => {
  it("asks for an upload on the patient route with the session token", async () => {
    fetchMock.mockResolvedValue(
      okResponse({ document_id: "doc-1", upload: UPLOAD_TARGET, max_bytes: 10 }),
    )

    await initOwnDocumentUpload(TOKEN, {
      filename: "card.png",
      mimeType: "image/png",
      sizeBytes: 2048,
      category: "message",
    })

    const [url, init] = lastCall()
    expect(url).toBe("http://test/api/patient/documents/init")
    expect(init.method).toBe("POST")
    expect(authHeader(init)).toBe(`Bearer ${TOKEN}`)
    expect(JSON.parse(init.body as string)).toEqual({
      filename: "card.png",
      mime_type: "image/png",
      size_bytes: 2048,
      category: "message",
    })
  })

  it("carries no patient id anywhere in the request", async () => {
    fetchMock.mockResolvedValue(
      okResponse({ document_id: "doc-1", upload: UPLOAD_TARGET, max_bytes: 10 }),
    )

    await initOwnDocumentUpload(TOKEN, {
      filename: "card.png",
      mimeType: "image/png",
      sizeBytes: 2048,
      category: "message",
    })

    const [url, init] = lastCall()
    expect(url).not.toContain("patients/")
    expect(JSON.parse(init.body as string)).not.toHaveProperty("patient_id")
  })

  it("escapes the document id it finalizes", async () => {
    fetchMock.mockResolvedValue(okResponse({ id: "doc 1" }))

    await finalizeOwnDocumentUpload(TOKEN, "doc 1")

    expect(lastCall()[0]).toBe("http://test/api/patient/documents/doc%201/finalize")
  })

  it("does the three steps in the order the routes require", async () => {
    fetchMock
      .mockResolvedValueOnce(
        okResponse({ document_id: "doc-1", upload: UPLOAD_TARGET, max_bytes: 10 }),
      )
      .mockResolvedValueOnce(okResponse({ id: "doc-1", filename: "card.png" }))
    const file = new File(["bytes"], "card.png", { type: "image/png" })

    const document = await uploadOwnDocument(TOKEN, file, "message")

    expect(uploadToStorage).toHaveBeenCalledWith(UPLOAD_TARGET, file)
    expect(fetchMock.mock.calls[0][0]).toBe("http://test/api/patient/documents/init")
    expect(fetchMock.mock.calls[1][0]).toBe(
      "http://test/api/patient/documents/doc-1/finalize",
    )
    expect(document.id).toBe("doc-1")
  })

  it("does not finalize an upload that never reached storage", async () => {
    fetchMock.mockResolvedValueOnce(
      okResponse({ document_id: "doc-1", upload: UPLOAD_TARGET, max_bytes: 10 }),
    )
    uploadToStorage.mockRejectedValue(new Error("storage is down"))
    const file = new File(["bytes"], "card.png", { type: "image/png" })

    await expect(uploadOwnDocument(TOKEN, file, "message")).rejects.toThrow()

    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it("asks for a download URL with the disposition it was given", async () => {
    fetchMock.mockResolvedValue(okResponse({ url: "http://storage/signed" }))

    const url = await getOwnDocumentDownloadUrl(TOKEN, "doc-1", "inline")

    expect(lastCall()[0]).toBe(
      "http://test/api/patient/documents/doc-1/file?disposition=inline",
    )
    expect(url).toBe("http://storage/signed")
  })

  it("throws a status and nothing else", async () => {
    fetchMock.mockResolvedValue(okResponse({ detail: "card.png was rejected" }, 422))

    await expect(finalizeOwnDocumentUpload(TOKEN, "doc-1")).rejects.toThrow(
      PatientDocumentsError,
    )
    await expect(
      finalizeOwnDocumentUpload(TOKEN, "doc-1"),
    ).rejects.not.toThrow(/card\.png/)
  })
})
