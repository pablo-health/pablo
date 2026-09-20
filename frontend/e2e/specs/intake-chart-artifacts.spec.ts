// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * What the practice sees of the files a form collected.
 *
 * `intake-artifacts.spec.ts` drives the patient's half through the
 * browser: three real file pickers, three uploads, and a form that goes
 * from outstanding to ready. This is the surface the clinician reads those
 * files back through, which is a different route with a different shape —
 * so the pickers are not driven again here. The files are put in the way
 * the portal puts them, through the shared upload fixture, and what is
 * under test starts at the chart.
 *
 * Two things, and neither is reachable from a route test:
 *
 * * the chart lists what arrived with enough about each file to decide
 *   whether to open it — the question's own wording, which side of the
 *   card, the name, the kind, the size the store reports — and never an id
 *   standing in for any of that;
 * * the file a clinician downloads is the file the patient sent, compared
 *   by SHA-256 rather than by "a file appeared". An upload that truncates,
 *   re-encodes or lands under the wrong key still produces a document row,
 *   and only identical bytes rule all three out.
 *
 * The three fixtures are deliberately different files, so a listing that
 * paired a name with the wrong row fails here instead of passing a count.
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
  const cardQuestion = version.items.find((item) => item.item_type === "insurance_card")!
  const recordsQuestion = version.items.find((item) => item.item_type === "document_request")!

  const assignment = await api.post<Assignment>(
    `/api/patients/${patient.id}/intake-assignments`,
    { version_id: version.id },
  )
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const portal = { Authorization: `Bearer ${sessionToken}` }

  // --- the patient sends two photographs and a document ------------------
  const front = fixtureFile("insurance-card.png", "image/png")
  const back = fixtureFile("insurance-card-back.png", "image/png")
  const records = fixtureFile("records.pdf", "application/pdf")

  const uploadedFront = await uploadAsPatient(request, sessionToken, front, "intake_artifact")
  const uploadedBack = await uploadAsPatient(request, sessionToken, back, "intake_artifact")
  const uploadedRecords = await uploadAsPatient(request, sessionToken, records, "intake_artifact")

  for (const attachment of [
    { item_id: cardQuestion.id, document_id: uploadedFront.id, side: "front" },
    { item_id: cardQuestion.id, document_id: uploadedBack.id, side: "back" },
    { item_id: recordsQuestion.id, document_id: uploadedRecords.id },
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
  expect(listed.map((row) => row.filename)).toEqual([front.name, back.name, records.name])
  expect(listed.map((row) => row.content_type)).toEqual([
    "image/png",
    "image/png",
    "application/pdf",
  ])
  // The size is the object's own, read back from storage at finalize,
  // rather than the number the client claimed on the way in.
  expect(listed.map((row) => row.size_bytes)).toEqual([
    front.body.length,
    back.body.length,
    records.body.length,
  ])
  // Nothing scans on this deployment, and absent is not "found clean".
  expect(listed.every((row) => row.scan_status === null)).toBe(true)

  // --- and what the clinician downloads is what the patient sent ---------
  // Every file, not just one: the three are different bytes, so a row
  // paired with the wrong object passes a count and fails a hash.
  for (const [row, sent] of [
    [listed[0], uploadedFront],
    [listed[1], uploadedBack],
    [listed[2], uploadedRecords],
  ] as const) {
    const link = await api.get<{ url: string }>(
      `/api/documents/${row.document_id}/file?disposition=inline`,
    )
    const fetched = await request.get(link.url)
    expect(fetched.status(), `the signed URL serves ${row.filename}`).toBe(200)
    expect(sha256(await fetched.body()), `${row.filename} survived the round trip`).toBe(
      sent.sha256,
    )
  }
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
