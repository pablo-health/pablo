// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A file a patient sends in, over the whole real path: the API signs an
 * upload target, the bytes go to object storage directly, the API reads the
 * object back to check it, and the clinician gets the same bytes out again.
 *
 * Every other layer of this already has coverage — the routes, the row
 * policies, the storage providers against fakes. What none of them can say
 * is whether the signed URL an API in one place hands to a browser in
 * another is one the browser can reach and the store will honour. That
 * claim needs a store, and this is the lane that has one.
 *
 * So the round trip is compared by hash rather than by "a file appeared":
 * an upload that truncates, re-encodes or lands under the wrong key still
 * produces a document row, and only the bytes coming back identical rules
 * all three out.
 *
 * Isolation is asserted the way the messaging spec asserts it, and for the
 * same reason: a second patient with a real session, throwing a real
 * document id at the surface that would serve it to its owner.
 */

import { expect, test } from "../fixtures/auth"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL, OBJECT_STORE_URL } from "../fixtures/stack"
import {
  fixtureFile,
  sendToUploadTarget,
  sha256,
  uploadAsPatient,
  type UploadTarget,
} from "../fixtures/upload"

const PATIENT_INIT = "/api/patient/documents/init"

interface DocumentResponse {
  id: string
  filename: string
  mime_type: string
  size_bytes: number
  uploaded_by: "patient" | "clinician"
  finalized_at: string | null
}

interface DocumentListResponse {
  data: DocumentResponse[]
  total: number
}

interface DownloadUrlResponse {
  url: string
}

interface InitResponse {
  document_id: string
  upload: UploadTarget
}

// --- The round trip ------------------------------------------------------

test("a patient's upload reaches the chart byte for byte @portal", async ({ api, request }) => {
  const card = fixtureFile("insurance-card.png", "image/png")
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)

  const uploaded = await uploadAsPatient(request, sessionToken, card, "intake_artifact")

  // The chart says where it came from. A document nobody at the practice
  // reviewed before it arrived is a different thing to read from one a
  // colleague filed, so this is not decoration.
  const listed = await api.get<DocumentListResponse>(`/api/patients/${patient.id}/documents`)
  const document = listed.data.find((d) => d.id === uploaded.id)
  expect(document, "the clinician's chart lists the patient's upload").toBeDefined()
  expect(document?.uploaded_by).toBe("patient")
  expect(document?.filename).toBe(card.name)
  expect(document?.mime_type).toBe("image/png")
  // The size on the row is the object's real size, read back from the
  // store at finalize — not the number the client claimed at init.
  expect(document?.size_bytes).toBe(card.body.length)
  expect(document?.finalized_at).not.toBeNull()

  const link = await api.get<DownloadUrlResponse>(
    `/api/documents/${uploaded.id}/file?disposition=inline`,
  )
  const fetched = await request.get(link.url)
  expect(fetched.status(), "the signed download URL serves the object").toBe(200)
  expect(sha256(await fetched.body()), "the bytes survive the round trip").toBe(uploaded.sha256)
})

// --- The URL the browser is given ---------------------------------------

test("the upload target names an address the browser can reach @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)

  const started = await request.post(`${BACKEND_URL}${PATIENT_INIT}`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
    data: {
      filename: "insurance-card.png",
      mime_type: "image/png",
      size_bytes: 115,
      category: "intake_artifact",
    },
  })
  expect(started.status()).toBe(201)
  const init = (await started.json()) as InitResponse

  // The store answers on one address inside the stack and another outside
  // it, and the signature covers whichever one was signed. A URL minted
  // against the inside address fails out here twice over, so the address is
  // worth asserting on its own: when it regresses, every upload in the
  // suite fails at once and none of them says why.
  expect(init.upload.url.startsWith(OBJECT_STORE_URL), init.upload.url).toBe(true)
})

// --- Somebody else's document -------------------------------------------

test("a second patient cannot reach the first one's document @portal", async ({
  api,
  request,
}) => {
  const owner = givePortalContactDetails()
  const ownerPatient = await givePatient(api, { email: owner.email, phone: owner.phone })
  const ownerSession = await givePortalSession(
    api,
    request,
    ownerPatient.id,
    owner.email,
    owner.phone,
  )
  const uploaded = await uploadAsPatient(
    request,
    ownerSession,
    fixtureFile("insurance-card.png", "image/png"),
    "intake_artifact",
  )

  const stranger = givePortalContactDetails()
  const strangerPatient = await givePatient(api, {
    email: stranger.email,
    phone: stranger.phone,
  })
  const strangerSession = await givePortalSession(
    api,
    request,
    strangerPatient.id,
    stranger.email,
    stranger.phone,
  )
  const strangerHeaders = { Authorization: `Bearer ${strangerSession}` }

  const strangerList = await request.get(`${BACKEND_URL}/api/patient/documents`, {
    headers: strangerHeaders,
  })
  expect(strangerList.status()).toBe(200)
  const strangersOwn = (await strangerList.json()) as DocumentListResponse
  expect(strangersOwn.data.map((d) => d.id)).not.toContain(uploaded.id)

  // 404, not 403: an id that belongs to somebody else and an id that never
  // existed have to answer the same way, or the difference between them is
  // readable from outside.
  const reached = await request.get(
    `${BACKEND_URL}/api/patient/documents/${uploaded.id}/file`,
    { headers: strangerHeaders },
  )
  expect(reached.status(), "another patient's document is not there").toBe(404)
})

// --- What will not be accepted ------------------------------------------

test("a file type the chart does not take is refused before anything is stored @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)

  const refused = await request.post(`${BACKEND_URL}${PATIENT_INIT}`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
    data: {
      // Named .png; it is text, and the type it declares is what it is.
      filename: "insurance-card.png",
      mime_type: "text/plain",
      size_bytes: 12,
      category: "intake_artifact",
    },
  })
  expect(refused.status(), "an unsupported type never gets an upload target").toBe(422)

  // And the other half of the same rule, which only a real store can show:
  // the accepted type is pinned in the signature, so a client that holds a
  // target for one type and declares another is turned away by the store,
  // without the API being asked at all.
  const started = await request.post(`${BACKEND_URL}${PATIENT_INIT}`, {
    headers: { Authorization: `Bearer ${sessionToken}` },
    data: {
      filename: "insurance-card.png",
      mime_type: "image/png",
      size_bytes: 12,
      category: "intake_artifact",
    },
  })
  expect(started.status()).toBe(201)
  const init = (await started.json()) as InitResponse
  const tampered: UploadTarget = {
    ...init.upload,
    headers: { ...init.upload.headers, "Content-Type": "text/plain" },
    fields: { ...init.upload.fields, "Content-Type": "text/plain" },
  }
  const mismatched = await sendToUploadTarget(request, tampered, {
    name: "insurance-card.png",
    mimeType: "text/plain",
    body: Buffer.from("not an image"),
  })
  expect(mismatched, "the store holds the signed content type").toBeGreaterThanOrEqual(400)
})
