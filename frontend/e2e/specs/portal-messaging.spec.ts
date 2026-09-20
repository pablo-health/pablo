// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The whole secure-messaging loop, over the real stack: a patient starts a
 * thread from the portal, a clinician reads and replies, and the patient
 * sees the reply and its own unread count settle back to zero.
 *
 * Two surfaces share one store, so this proves both: the patient's own
 * routes (`/api/patient/messages/...`) and the clinician's
 * (`/api/patients/{id}/message-threads`, `/api/message-threads/{id}...`).
 * Isolation is the point an end-to-end test is uniquely positioned to make
 * — a second patient's session, and a clinician's own bearer, thrown at the
 * wrong surface, over real HTTP.
 *
 * Each test invites and redeems its own patient, through the same mail and
 * text-message capture seams `portal-auth.spec.ts` uses (factored into
 * `fixtures/portal.ts`), so no test reads a credential meant for another.
 */

import { expect, test } from "../fixtures/auth"
import { signInWithPassword } from "../fixtures/api"
import {
  givePortalContactDetails,
  givePortalInvitation,
  givePortalSession,
  redeemPortalInvitation,
  signInToPortal,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { BACKEND_URL } from "../fixtures/stack"

const PATIENT_THREADS = "/api/patient/messages/threads"

interface MessageResponse {
  id: string
  thread_id: string
  sender: string
  body: string
  created_at: string
  read_at: string | null
}

interface ThreadResponse {
  id: string
  subject: string | null
  status: string
  created_at: string
  last_message_at: string
  unread_count: number | null
}

interface ThreadDetailResponse extends ThreadResponse {
  messages: MessageResponse[]
}

interface ThreadListResponse {
  data: ThreadResponse[]
  total: number
}

// --- Test 1: patient starts a thread; the clinician's list picks it up ----

test("a patient starts a thread and the clinician's list picks it up @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const started = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers,
    data: { subject: "A question about my next visit", body: "Can we move Thursday?" },
  })
  expect(started.status(), "starting a thread with a first message").toBe(201)
  const thread = (await started.json()) as ThreadDetailResponse
  expect(thread.messages).toHaveLength(1)
  expect(thread.messages[0].sender).toBe("patient")

  const patientList = (await (
    await request.get(`${BACKEND_URL}${PATIENT_THREADS}`, { headers })
  ).json()) as ThreadListResponse
  expect(patientList.data.map((row) => row.id)).toContain(thread.id)
  expect(patientList.data.find((row) => row.id === thread.id)?.unread_count).toBe(0)

  const clinicianList = await api.get<ThreadListResponse>(
    `/api/patients/${patient.id}/message-threads`,
  )
  expect(clinicianList.data.map((row) => row.id)).toContain(thread.id)
})

// --- Test 2: clinician reads and replies; unread settles back to zero -----

test("a clinician reply reaches the patient, and unread settles back to zero @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const sessionToken = await givePortalSession(api, request, patient.id, email, phone)
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const started = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers,
    data: { subject: "Refill", body: "Could I get a refill before Friday?" },
  })
  const threadId = ((await started.json()) as ThreadDetailResponse).id

  const opened = await api.get<ThreadDetailResponse>(`/api/message-threads/${threadId}`)
  expect(opened.messages).toHaveLength(1)

  const reply = await api.post<MessageResponse>(`/api/message-threads/${threadId}/replies`, {
    body: "Yes — I'll send it in now.",
  })
  expect(reply.sender).toBe("clinician")

  const detail = (await (
    await request.get(`${BACKEND_URL}${PATIENT_THREADS}/${threadId}`, { headers })
  ).json()) as ThreadDetailResponse
  expect(detail.messages.map((m) => m.sender)).toEqual(["patient", "clinician"])

  const listRow = async (): Promise<ThreadResponse | undefined> => {
    const list = (await (
      await request.get(`${BACKEND_URL}${PATIENT_THREADS}`, { headers })
    ).json()) as ThreadListResponse
    return list.data.find((row) => row.id === threadId)
  }

  expect((await listRow())?.unread_count, "the reply is unread until the patient opens it").toBe(
    1,
  )

  const read = await request.post(`${BACKEND_URL}${PATIENT_THREADS}/${threadId}/read`, {
    headers,
  })
  expect(read.status()).toBe(200)
  expect((await read.json()).marked_read).toBe(1)
  expect((await listRow())?.unread_count).toBe(0)

  const secondRead = await request.post(`${BACKEND_URL}${PATIENT_THREADS}/${threadId}/read`, {
    headers,
  })
  expect((await secondRead.json()).marked_read, "nothing left to mark the second time").toBe(0)
})

// --- Test 3: isolation over HTTP, control before every negative -----------

test("one patient's thread is a 404 to another, and each surface refuses the other's principal @portal", async ({
  api,
  onboardedUser,
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

  const startedA = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers: headersA,
    data: { subject: null, body: "Only for my own clinician" },
  })
  const threadAId = ((await startedA.json()) as ThreadDetailResponse).id

  const startedB = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers: headersB,
    data: { subject: null, body: "A different patient's own thread" },
  })
  const threadBId = ((await startedB.json()) as ThreadDetailResponse).id

  // Control first: A can reach A's own thread, so the refusals below are not vacuous.
  const own = await request.get(`${BACKEND_URL}${PATIENT_THREADS}/${threadAId}`, {
    headers: headersA,
  })
  expect(own.status(), "A can read A's own thread").toBe(200)

  const getAsB = await request.get(`${BACKEND_URL}${PATIENT_THREADS}/${threadAId}`, {
    headers: headersB,
    failOnStatusCode: false,
  })
  expect(getAsB.status(), "B gets no existence oracle for A's thread").toBe(404)

  const postAsB = await request.post(`${BACKEND_URL}${PATIENT_THREADS}/${threadAId}/messages`, {
    headers: headersB,
    data: { body: "not mine to send into" },
    failOnStatusCode: false,
  })
  expect(postAsB.status()).toBe(404)

  const readAsB = await request.post(`${BACKEND_URL}${PATIENT_THREADS}/${threadAId}/read`, {
    headers: headersB,
    failOnStatusCode: false,
  })
  expect(readAsB.status()).toBe(404)

  const listAsA = (await (
    await request.get(`${BACKEND_URL}${PATIENT_THREADS}`, { headers: headersA })
  ).json()) as ThreadListResponse
  expect(listAsA.data.map((row) => row.id)).not.toContain(threadBId)

  // A patient session is refused the clinician surface — the same
  // principal-separation claim portal-auth.spec.ts makes for intake.
  const clinicianRouteAsPatient = await request.get(
    `${BACKEND_URL}/api/patients/${patientA.id}/message-threads`,
    { headers: headersA, failOnStatusCode: false },
  )
  expect(
    [401, 403],
    `a patient session is refused the clinician route (got ${clinicianRouteAsPatient.status()})`,
  ).toContain(clinicianRouteAsPatient.status())

  // And the other direction: a real clinician bearer opens no patient
  // route. It is signed by a different issuer than a patient session, so
  // verification fails before anything about grants is even asked.
  const clinicianIdToken = await signInWithPassword(onboardedUser.email, onboardedUser.password)
  const patientRouteAsClinician = await request.get(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers: { Authorization: `Bearer ${clinicianIdToken}` },
    failOnStatusCode: false,
  })
  expect(
    patientRouteAsClinician.status(),
    "a clinician bearer resolves to no patient principal",
  ).toBe(401)
})

// --- Test 4: credential hygiene --------------------------------------------

test("neither the invite token nor the step-up code ever comes back in a message response @portal", async ({
  api,
  request,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const invitation = await givePortalInvitation(api, patient.id, email, phone)
  const sessionToken = await redeemPortalInvitation(request, invitation)
  const headers = { Authorization: `Bearer ${sessionToken}` }

  const started = await request.post(`${BACKEND_URL}${PATIENT_THREADS}`, {
    headers,
    data: { subject: "Credential hygiene", body: "This body is not a secret." },
  })
  const thread = (await started.json()) as ThreadDetailResponse

  const listText = await (
    await request.get(`${BACKEND_URL}${PATIENT_THREADS}`, { headers })
  ).text()
  const detailText = await (
    await request.get(`${BACKEND_URL}${PATIENT_THREADS}/${thread.id}`, { headers })
  ).text()
  const clinicianList = await api.get<ThreadListResponse>(
    `/api/patients/${patient.id}/message-threads`,
  )
  const clinicianDetail = await api.get<ThreadDetailResponse>(
    `/api/message-threads/${thread.id}`,
  )

  const bodies = [
    JSON.stringify(thread),
    listText,
    detailText,
    JSON.stringify(clinicianList),
    JSON.stringify(clinicianDetail),
  ]
  for (const body of bodies) {
    expect(body).not.toContain(invitation.token)
    expect(body).not.toContain(invitation.otp)
  }
})

// --- Test 5: the module in the shell, from a real browser ------------------

test("the messaging module renders, warns about 988, and the composer's send reaches the clinician @portal", async ({
  api,
  page,
}) => {
  const { email, phone } = givePortalContactDetails()
  const patient = await givePatient(api, { email, phone })
  const invitation = await givePortalInvitation(api, patient.id, email, phone)

  await signInToPortal(page, invitation)

  const notice = page.getByTestId("portal-messaging-expectation-notice")
  await expect(notice).toBeVisible()
  await expect(notice).toContainText("988")

  await page.getByTestId("portal-messaging-start-thread").click()
  await page.getByTestId("portal-messaging-new-thread-subject").fill("From the portal")
  await page
    .getByTestId("portal-messaging-new-thread-body")
    .fill("Sending this from the portal composer.")
  await page.getByTestId("portal-messaging-new-thread-send").click()

  await expect(page.getByTestId("portal-messaging-thread-view")).toBeVisible()
  await expect(page.getByTestId("portal-messaging-thread-view")).toContainText(
    "Sending this from the portal composer.",
  )

  await page.getByTestId("portal-messaging-composer-body").fill("And one more thing.")
  await page.getByTestId("portal-messaging-composer-send").click()
  await expect(page.getByTestId("portal-messaging-thread-view")).toContainText(
    "And one more thing.",
  )

  const clinicianList = await api.get<ThreadListResponse>(
    `/api/patients/${patient.id}/message-threads`,
  )
  expect(clinicianList.data).toHaveLength(1)
  const threadId = clinicianList.data[0].id

  const clinicianDetail = await api.get<ThreadDetailResponse>(
    `/api/message-threads/${threadId}`,
  )
  expect(clinicianDetail.messages.map((m) => m.body)).toEqual([
    "Sending this from the portal composer.",
    "And one more thing.",
  ])
  expect(clinicianDetail.messages.every((m) => m.sender === "patient")).toBe(true)
})
