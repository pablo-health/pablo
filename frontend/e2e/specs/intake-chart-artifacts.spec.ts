// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A file the patient sent in with a form, followed to the clinician's chart.
 *
 * `intake-artifacts.spec.ts` proves the patient's half: what a form that
 * asks for files renders, and that it cannot be handed in until they
 * arrive. This is the other end of the same journey, and it needs a real
 * store to exist at all — the bytes go browser to storage directly, so
 * until this stack ran one the only thing a browser could be shown was a
 * 503.
 *
 * Two things a browser is uniquely positioned to prove, and neither is
 * reachable from a route test:
 *
 * * the chart lists what arrived, named by the question that asked for it
 *   and by which side of the card it is — not by an id;
 * * the file a clinician downloads is the file the patient sent, compared
 *   by SHA-256 rather than by "a file appeared". An upload that truncates,
 *   re-encodes or lands under the wrong key still produces a document row,
 *   and only identical bytes rule all three out.
 *
 * Both factors of the patient's sign-in come from the stand-in their
 * channel is wired to: the link out of the mail server, the step-up code
 * out of the text-message gateway.
 */

import { expect, test } from "../fixtures/auth"
import { ApiError, type ApiClient } from "../fixtures/api"
import { givePortalContactDetails, givePortalSession } from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"
import { fixtureFile, sha256, uploadAsPatient } from "../fixtures/upload"

interface IntakeVersion {
  id: string
  published_at: string | null
}

interface IntakeTemplate {
  id: string
  name: string
  versions: IntakeVersion[]
}

interface IntakeItem {
  id: string
  key: string
  item_type: string
}

interface IntakeVersionDetail extends IntakeVersion {
  template_id: string
  items: IntakeItem[]
}

interface Assignment {
  id: string
  status: string
}

interface ChartArtifact {
  id: string
  item_id: string
  item_label: string
  side: string | null
  document_id: string
  filename: string
  content_type: string
  size_bytes: number
  scan_status: string | null
  created_at: string
}

const CARD_QUESTION = "A photo of your insurance card"
const RECORDS_QUESTION = "Any records from a previous provider"

/** Publish a form of the practice's own that asks for files. */
async function publishFileForm(api: ApiClient): Promise<IntakeVersionDetail> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Before we meet ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        {
          key: "card",
          item_type: "insurance_card",
          label: CARD_QUESTION,
          config: { sides: "both" },
        },
        {
          key: "records",
          item_type: "document_request",
          label: RECORDS_QUESTION,
          config: {},
        },
      ],
    },
  )

  const published = await api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
  expect(published.published_at, "a form is only ever sent frozen").not.toBeNull()
  return published
}

test("a file a patient attaches to a form reaches the chart byte for byte @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone, date_of_birth: "1988-11-02" })
  const version = await publishFileForm(api)
  const card = version.items.find((item) => item.item_type === "insurance_card")!
  const records = version.items.find((item) => item.item_type === "document_request")!

  const assignment = await api.post<Assignment>(
    `/api/patients/${patient.id}/intake-assignments`,
    { version_id: version.id },
  )
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const portal = { Authorization: `Bearer ${sessionToken}` }

  // --- the patient sends two photographs and a document ------------------
  const front = fixtureFile("insurance-card.png", "image/png")
  const uploadedFront = await uploadAsPatient(request, sessionToken, front, "intake_artifact")
  const uploadedBack = await uploadAsPatient(
    request,
    sessionToken,
    { ...front, name: "card-back.png" },
    "intake_artifact",
  )
  const uploadedRecords = await uploadAsPatient(
    request,
    sessionToken,
    { ...front, name: "referral.png" },
    "intake_artifact",
  )

  for (const attachment of [
    { item_id: card.id, document_id: uploadedFront.id, side: "front" },
    { item_id: card.id, document_id: uploadedBack.id, side: "back" },
    { item_id: records.id, document_id: uploadedRecords.id },
  ]) {
    const attached = await request.post(
      `${BACKEND_URL}/api/patient/intake/assignments/${assignment.id}/artifacts`,
      { headers: portal, data: attachment },
    )
    expect(attached.status(), `attaching ${attachment.document_id}`).toBe(201)
  }

  // --- and the chart lists them, named by the question -------------------
  const listed = await api.get<ChartArtifact[]>(
    `/api/patients/${patient.id}/intake-assignments/${assignment.id}/artifacts`,
  )
  expect(listed.map((row) => row.document_id)).toEqual([
    uploadedFront.id,
    uploadedBack.id,
    uploadedRecords.id,
  ])
  expect(listed.map((row) => row.side)).toEqual(["front", "back", null])
  expect(listed.map((row) => row.item_label)).toEqual([
    CARD_QUESTION,
    CARD_QUESTION,
    RECORDS_QUESTION,
  ])
  expect(listed[0].filename).toBe("insurance-card.png")
  expect(listed[0].content_type).toBe("image/png")
  // The size is the object's own, read back from storage at finalize,
  // rather than the number the client claimed on the way in.
  expect(listed[0].size_bytes).toBe(front.body.length)
  // Nothing scans on this deployment, and absent is not "found clean".
  expect(listed.every((row) => row.scan_status === null)).toBe(true)

  // --- and what the clinician downloads is what the patient sent ---------
  const link = await api.get<{ url: string }>(
    `/api/documents/${uploadedFront.id}/file?disposition=inline`,
  )
  const fetched = await request.get(link.url)
  expect(fetched.status(), "the signed download URL serves the object").toBe(200)
  expect(sha256(await fetched.body()), "the bytes survive the round trip").toBe(
    uploadedFront.sha256,
  )
})

test("a form on another patient's chart hands back nothing @portal", async ({ api }) => {
  const owner = givePortalContactDetails()
  const ownerPatient = await givePatient(api, { email: owner.email, phone: owner.phone })
  const version = await publishFileForm(api)

  const assignment = await api.post<Assignment>(
    `/api/patients/${ownerPatient.id}/intake-assignments`,
    { version_id: version.id },
  )

  const stranger = givePortalContactDetails()
  const strangerPatient = await givePatient(api, {
    email: stranger.email,
    phone: stranger.phone,
  })

  // Control first, so the refusal below is about whose chart the path
  // names and not about an assignment that was never there.
  const own = await api.get<ChartArtifact[]>(
    `/api/patients/${ownerPatient.id}/intake-assignments/${assignment.id}/artifacts`,
  )
  expect(own).toEqual([])

  // 404, not 403: the id names a form, and the path must not say whose.
  const reached = await api
    .get<ChartArtifact[]>(
      `/api/patients/${strangerPatient.id}/intake-assignments/${assignment.id}/artifacts`,
    )
    .then(() => null)
    .catch((error: unknown) => error)
  expect(reached, "another patient's form is not reachable").toBeInstanceOf(ApiError)
  expect((reached as ApiError).status).toBe(404)
})
