// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A file a patient sends in that is not an attachment on a message: an
 * insurance card, a letter, whatever the practice asked for before a first
 * appointment. It goes up the same way an attachment does and then lands
 * somewhere different — on the chart's own document list, where a clinician
 * reads it.
 *
 * `portal-messaging-attachments.spec.ts` already follows the messaging half
 * of this over a real store, so what is left here is the half it does not
 * touch: the intake-artifact category, the chart list that says a document
 * came from the patient rather than from a colleague, and the two refusals
 * on the way in.
 *
 * The round trip is compared by hash rather than by "a file appeared". An
 * upload that truncates, re-encodes or lands under the wrong key still
 * produces a document row, and only identical bytes coming back rule all
 * three out.
 */

import { expect, test } from "../fixtures/auth"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"
import {
  fixtureFile,
  sendToUploadTarget,
  sha256,
  uploadAsPatient,
  type UploadTarget,
} from "../fixtures/upload"

const PATIENT_DOCUMENTS = "/api/patient/documents"

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

interface InitResponse {
  document_id: string
  upload: UploadTarget
}

// --- The chart's own copy ------------------------------------------------

test("an intake artifact reaches the chart byte for byte @portal", async ({ api, request }) => {
  const card = fixtureFile("insurance-card.png", "image/png")
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)

  const uploaded = await uploadAsPatient(request, sessionToken, card, "intake_artifact")

  const listed = await api.get<DocumentListResponse>(`/api/patients/${patient.id}/documents`)
  const document = listed.data.find((d) => d.id === uploaded.id)
  expect(document, "the clinician's chart lists the patient's upload").toBeDefined()
  // A document nobody at the practice reviewed before it arrived is a
  // different thing to read from one a colleague filed, so the chart says
  // which rather than leaving it to be inferred from a missing field.
  expect(document?.uploaded_by).toBe("patient")
  expect(document?.filename).toBe(card.name)
  expect(document?.mime_type).toBe("image/png")
  // The size on the row is the object's real size, read back from storage
  // at finalize, not the number the client claimed at init.
  expect(document?.size_bytes).toBe(card.body.length)
  expect(document?.finalized_at).not.toBeNull()

  const link = await api.get<{ url: string }>(
    `/api/documents/${uploaded.id}/file?disposition=inline`,
  )
  const fetched = await request.get(link.url)
  expect(fetched.status(), "the signed download URL serves the object").toBe(200)
  expect(sha256(await fetched.body()), "the bytes survive the round trip").toBe(uploaded.sha256)
})

// --- Somebody else's intake artifact ------------------------------------

test("a second patient cannot reach the first one's intake artifact @portal", async ({
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

  // Control first, so the refusal below is about who is asking and not
  // about a document that was never there.
  const own = await request.get(`${BACKEND_URL}${PATIENT_DOCUMENTS}/${uploaded.id}/file`, {
    headers: { Authorization: `Bearer ${ownerSession}` },
  })
  expect(own.status(), "the owner can fetch their own artifact").toBe(200)

  // 404, not 403: an id belonging to somebody else and an id that never
  // existed have to answer alike, or the difference is readable from
  // outside.
  const reached = await request.get(`${BACKEND_URL}${PATIENT_DOCUMENTS}/${uploaded.id}/file`, {
    headers: strangerHeaders,
    failOnStatusCode: false,
  })
  expect(reached.status(), "another patient's artifact is not there").toBe(404)

  const strangersOwn = (await (
    await request.get(`${BACKEND_URL}${PATIENT_DOCUMENTS}`, { headers: strangerHeaders })
  ).json()) as DocumentListResponse
  expect(strangersOwn.data.map((d) => d.id)).not.toContain(uploaded.id)
})

// --- What will not be accepted ------------------------------------------

test("a file type the chart does not take is refused before anything is stored @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const refused = await request.post(`${BACKEND_URL}${PATIENT_DOCUMENTS}/init`, {
    headers,
    // Named .png; it is text, and the type it declares is what it is. The
    // engine does not sniff bytes against the declared type, so this is the
    // refusal it actually makes — no upload target is ever minted.
    data: {
      filename: "insurance-card.png",
      mime_type: "text/plain",
      size_bytes: 12,
      category: "intake_artifact",
    },
    failOnStatusCode: false,
  })
  expect(refused.status(), "an unsupported type never gets an upload target").toBe(422)

  // And the other half of the same rule, which only a real store can show:
  // the accepted type is pinned in the signature, so a client holding a
  // target for one type and declaring another is turned away by storage,
  // without the API being asked at all.
  const started = await request.post(`${BACKEND_URL}${PATIENT_DOCUMENTS}/init`, {
    headers,
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
  expect(mismatched, "storage holds the signed content type").toBeGreaterThanOrEqual(400)
})
