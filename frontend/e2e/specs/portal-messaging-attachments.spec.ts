// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A file sent on a secure message, over the real stack, both directions.
 *
 * The claim only an end-to-end test can make here is that the BYTES survive
 * the round trip. A patient's upload goes browser→storage against a signed
 * URL, and the clinician's download comes back the same way — three
 * different processes and a real object store between them, none of which a
 * route test can see. So the assertion is a SHA-256 of what came out
 * against a SHA-256 of what went in: a test that only checked the filename
 * would pass against a store that returned somebody else's file.
 *
 * Isolation is the other thing this is uniquely positioned to show. A
 * second patient's session, thrown at the first patient's document and at
 * the first patient's thread, over real HTTP.
 *
 * Each test invites and redeems its own patient through `fixtures/portal.ts`,
 * the same capture seam `portal-messaging.spec.ts` uses, so no test reads a
 * credential meant for another.
 */

import { createHash } from "node:crypto"
import { expect, test } from "../fixtures/auth"
import {
  givePortalContactDetails,
  givePortalInvitation,
  givePortalSession,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"
import type { APIRequestContext } from "@playwright/test"

const PATIENT_THREADS = "/api/patient/messages/threads"
const PATIENT_DOCUMENTS = "/api/patient/documents"

/**
 * The smallest real PNG: an 8-bit RGBA 1x1. Built here rather than read
 * from a fixture file so the bytes under test and the bytes being hashed
 * are unarguably the same object.
 */
const PNG_BYTES = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
  "base64",
)

function sha256(bytes: Buffer): string {
  return createHash("sha256").update(bytes).digest("hex")
}

interface UploadTarget {
  url: string
  method: "PUT" | "POST"
  headers: Record<string, string>
  fields: Record<string, string>
}

interface InitResponse {
  document_id: string
  upload: UploadTarget
  max_bytes: number
}

interface AttachmentResponse {
  document_id: string
  filename: string
  mime_type: string
  size_bytes: number
}

interface MessageResponse {
  id: string
  sender: string
  body: string
  attachments: AttachmentResponse[]
}

interface ThreadDetailResponse {
  id: string
  messages: MessageResponse[]
}

/**
 * The patient's whole upload: init, the browser's own PUT or POST to
 * storage, finalize. Returns the finalized document id, which is the only
 * state a message may attach.
 */
async function givePatientDocument(
  request: APIRequestContext,
  sessionToken: string,
  filename: string,
): Promise<string> {
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const started = await request.post(`${BACKEND_URL}${PATIENT_DOCUMENTS}/init`, {
    headers,
    data: {
      filename,
      mime_type: "image/png",
      size_bytes: PNG_BYTES.length,
      category: "message",
    },
  })
  expect(started.status(), await started.text()).toBe(201)
  const init = (await started.json()) as InitResponse

  // Executed exactly as the browser client executes it, recipe and all —
  // the signature covers the method, the headers and the object name, so a
  // shortcut here would not be testing the same request.
  const stored =
    init.upload.method === "POST"
      ? await request.post(init.upload.url, {
          multipart: { ...init.upload.fields, file: {
            name: filename,
            mimeType: "image/png",
            buffer: PNG_BYTES,
          } },
        })
      : await request.put(init.upload.url, {
          headers: init.upload.headers,
          data: PNG_BYTES,
        })
  expect(stored.ok(), `storage accepted the upload: ${await stored.text()}`).toBeTruthy()

  const finalized = await request.post(
    `${BACKEND_URL}${PATIENT_DOCUMENTS}/${init.document_id}/finalize`,
    { headers },
  )
  expect(finalized.status(), await finalized.text()).toBe(200)
  return init.document_id
}

// --- Test 1: the round trip, byte for byte -------------------------------

test("a patient's file reaches the clinician unchanged @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const documentId = await givePatientDocument(request, sessionToken, "card.png")

  const started = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers,
    data: {
      subject: "The card you asked for",
      body: "Attaching it here.",
      attachment_ids: [documentId],
    },
  })
  expect(started.status(), await started.text()).toBe(201)
  const thread = (await started.json()) as ThreadDetailResponse
  expect(thread.messages[0].attachments).toEqual([
    {
      document_id: documentId,
      filename: "card.png",
      mime_type: "image/png",
      size_bytes: PNG_BYTES.length,
    },
  ])

  // The clinician opens the thread and sees the same file listed.
  const opened = await api.get<ThreadDetailResponse>(
    `/api/message-threads/${thread.id}`,
  )
  expect(opened.messages[0].attachments.map((a) => a.document_id)).toEqual([documentId])

  // And downloads it. The signed URL authorizes the fetch on its own, so it
  // is followed without a bearer token — which is the whole point of it.
  const link = await api.get<{ url: string }>(`/api/documents/${documentId}/file`)
  const fetched = await request.get(link.url)
  expect(fetched.status()).toBe(200)

  expect(
    sha256(await fetched.body()),
    "the bytes the clinician downloaded are the bytes the patient sent",
  ).toBe(sha256(PNG_BYTES))
})

// --- Test 2: the clinician's reply carries a file back -------------------

test("a clinician's own file comes back down the same thread @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const started = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers,
    data: { subject: "Summary", body: "Could I have the summary?" },
  })
  const threadId = ((await started.json()) as ThreadDetailResponse).id

  const init = await api.post<InitResponse>(
    `/api/patients/${patient.id}/documents/init`,
    {
      filename: "summary.png",
      mime_type: "image/png",
      size_bytes: PNG_BYTES.length,
      category: "message",
    },
  )
  const stored =
    init.upload.method === "POST"
      ? await request.post(init.upload.url, {
          multipart: { ...init.upload.fields, file: {
            name: "summary.png",
            mimeType: "image/png",
            buffer: PNG_BYTES,
          } },
        })
      : await request.put(init.upload.url, {
          headers: init.upload.headers,
          data: PNG_BYTES,
        })
  expect(stored.ok()).toBeTruthy()
  await api.post(`/api/documents/${init.document_id}/finalize`)

  const reply = await api.post<MessageResponse>(
    `/api/message-threads/${threadId}/replies`,
    { body: "Here it is.", attachment_ids: [init.document_id] },
  )
  expect(reply.attachments.map((a) => a.filename)).toEqual(["summary.png"])

  // The patient reads it back, and fetches it through their OWN route.
  const detail = (await (
    await request.get(`${BACKEND_URL}${PATIENT_THREADS}/${threadId}`, { headers })
  ).json()) as ThreadDetailResponse
  const delivered = detail.messages[detail.messages.length - 1].attachments
  expect(delivered.map((a) => a.document_id)).toEqual([init.document_id])

  const link = (await (
    await request.get(
      `${BACKEND_URL}${PATIENT_DOCUMENTS}/${init.document_id}/file`,
      { headers },
    )
  ).json()) as { url: string }
  const fetched = await request.get(link.url)
  expect(sha256(await fetched.body())).toBe(sha256(PNG_BYTES))

  // The chart says which conversation it came from.
  const chart = await api.get<{
    data: { id: string; message_thread_id: string | null }[]
  }>(`/api/patients/${patient.id}/documents`)
  const row = chart.data.find((d) => d.id === init.document_id)
  expect(row?.message_thread_id).toBe(threadId)
})

// --- Test 3: isolation, control before every negative ---------------------

test("a second patient reaches neither the file nor the thread it came on @portal", async ({
  api,
  request,
}) => {
  const a = givePortalContactDetails()
  const patientA = await givePatient(api, { email: a.email, phone: a.phone })
  const tokenA = await givePortalSession(api, request, patientA.id, a.email, a.phone)
  const headersA = { Authorization: `Bearer ${tokenA}` }

  const b = givePortalContactDetails()
  const patientB = await givePatient(api, { email: b.email, phone: b.phone })
  const tokenB = await givePortalSession(api, request, patientB.id, b.email, b.phone)
  const headersB = { Authorization: `Bearer ${tokenB}` }

  const documentId = await givePatientDocument(request, tokenA, "private.png")
  const started = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers: headersA,
    data: { subject: null, body: "Only mine", attachment_ids: [documentId] },
  })
  const threadId = ((await started.json()) as ThreadDetailResponse).id

  // Control first: A reaches their own file, so every refusal below is
  // about who is asking and not about a document that is missing.
  const own = await request.get(
    `${BACKEND_URL}${PATIENT_DOCUMENTS}/${documentId}/file`,
    { headers: headersA },
  )
  expect(own.status(), "A can fetch A's own attachment").toBe(200)

  const asB = await request.get(
    `${BACKEND_URL}${PATIENT_DOCUMENTS}/${documentId}/file`,
    { headers: headersB, failOnStatusCode: false },
  )
  expect(asB.status(), "B gets no existence oracle for A's file").toBe(404)

  const threadAsB = await request.get(`${BACKEND_URL}${PATIENT_THREADS}/${threadId}`, {
    headers: headersB,
    failOnStatusCode: false,
  })
  expect(threadAsB.status()).toBe(404)

  // And B cannot send A's document on a message of their own.
  const bThread = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers: headersB,
    data: { subject: null, body: "Not mine to send", attachment_ids: [documentId] },
    failOnStatusCode: false,
  })
  expect(bThread.status(), "another patient's document is not attachable").toBe(422)
  expect(await bThread.text()).not.toContain("private.png")

  // B's own documents are absent from A's list, and A's from B's.
  const listB = (await (
    await request.get(`${BACKEND_URL}${PATIENT_DOCUMENTS}`, { headers: headersB })
  ).json()) as { data: { id: string }[] }
  expect(listB.data.map((d) => d.id)).not.toContain(documentId)
})

// --- Test 4: what a send refuses -----------------------------------------

test("a file already sent cannot be sent again @portal", async ({ api, request }) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const documentId = await givePatientDocument(request, sessionToken, "once.png")

  const first = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers,
    data: { subject: null, body: "First", attachment_ids: [documentId] },
  })
  expect(first.status(), "the control: the first send is accepted").toBe(201)

  const second = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers,
    data: { subject: null, body: "Again", attachment_ids: [documentId] },
    failOnStatusCode: false,
  })
  expect(second.status()).toBe(422)
  expect((await second.json()).error.code).toBe("ATTACHMENT_ALREADY_SENT")
})

// --- Test 5: the portal composer, from a real browser --------------------

test("the portal composer attaches a file and the clinician receives it @portal", async ({
  api,
  page,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const invitation = await givePortalInvitation(api, patient.id, email, phone)

  await signInToPortal(page, invitation)

  await page.getByTestId("portal-messaging-start-thread").click()
  await page.getByTestId("portal-messaging-new-thread-subject").fill("With a file")
  await page.getByTestId("portal-messaging-new-thread-body").fill("Opening the thread.")
  await page.getByTestId("portal-messaging-new-thread-send").click()
  await expect(page.getByTestId("portal-messaging-thread-view")).toBeVisible()

  await page.getByTestId("portal-messaging-composer-file").setInputFiles({
    name: "from-the-browser.png",
    mimeType: "image/png",
    buffer: PNG_BYTES,
  })
  await expect(
    page.getByTestId("portal-messaging-composer-attachments"),
    "the chip appears once the upload has finished",
  ).toContainText("from-the-browser.png")

  await page.getByTestId("portal-messaging-composer-body").fill("Here is the file.")
  await page.getByTestId("portal-messaging-composer-send").click()
  await expect(page.getByTestId("portal-messaging-thread-view")).toContainText(
    "Here is the file.",
  )

  const threads = await api.get<{ data: { id: string }[] }>(
    `/api/patients/${patient.id}/message-threads`,
  )
  const detail = await api.get<ThreadDetailResponse>(
    `/api/message-threads/${threads.data[0].id}`,
  )
  const sent = detail.messages.flatMap((m) => m.attachments)
  expect(sent.map((a) => a.filename)).toEqual(["from-the-browser.png"])

  const link = await api.get<{ url: string }>(
    `/api/documents/${sent[0].document_id}/file`,
  )
  const fetched = await page.request.get(link.url)
  expect(sha256(await fetched.body())).toBe(sha256(PNG_BYTES))
})
