// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Intake, from the form a practice builds to the file it keeps.
 *
 * The specs beside this one each prove a stretch of that road: the walk
 * (`portal-forms`), the files (`intake-artifacts`), the signature
 * (`portal-consent`), the round trip (`intake-review`), the document
 * (`intake-export`), the chart (`intake-chart-artifacts`). Every one of them
 * is true of a form with two or three questions on it. What none of them can
 * say is that the whole thing holds together on a real packet — nine
 * questions of eight kinds, two of which are answered by photographing
 * something, one by signing it, one nobody has to answer at all — and that
 * a person meeting it on a phone can put it down, come back and finish it.
 *
 * So this is one journey rather than a set of assertions, and the order is
 * the point. A packet assigned, delivered, half answered, abandoned, resumed
 * on a second sign-in, refused when it is handed in early, finished,
 * received, read back, sent back, corrected, written into, accepted, and
 * exported as one file a practice can put in a drawer.
 *
 * **The journey starts on a screen.** Sending the form and inviting the
 * patient to open it are one press on the chart, because that is the only
 * part of this a clinician cannot do any other way — a route can be called
 * by a test forever and still have no button. The rest of the clinician's
 * half is still driven at the API: writing an answer down for somebody in
 * the room, asking for a correction, accepting. Those have screens now and
 * converting them is worth doing, but each is its own walk and this one is
 * already long.
 *
 * What a browser is otherwise here for is the patient's half, where the
 * screens are the only place the walk can be seen at all — plus the
 * clinician surfaces that render either way, the chart's file list and the
 * plan read off a photographed card.
 *
 * **Both factors are read from the stand-in their channel is wired to**: the
 * link out of the mail server, the step-up code out of the text-message
 * gateway. Both are real sends through the real service; only the last hop
 * is a fake.
 */

import type { APIRequestContext, Page } from "@playwright/test"
import { expect, test } from "../fixtures/auth"
import { signInWithPassword, type ApiClient } from "../fixtures/api"
import { answerMeasureOnScreen } from "../fixtures/intake"
import { firstLink, mail } from "../fixtures/mail"
import {
  givePortalContactDetails,
  givePortalSession,
  signInToPortal,
  type PortalInvitation,
} from "../fixtures/portal"
import { givePatient } from "../fixtures/scenarios"
import { sms, stepUpCode } from "../fixtures/sms"
import { BACKEND_URL } from "../fixtures/stack"
import {
  fixtureFile,
  sendToUploadTarget,
  sha256,
  toInputFile,
  type UploadFile,
  type UploadTarget,
} from "../fixtures/upload"

// --- what the server hands back -------------------------------------------

interface IntakeDocument {
  id: string
  document_key: string
  title: string
  version: number
  digest: string
  published_at: string | null
}

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
  required: boolean
  label: string | null
  config: Record<string, unknown>
}

interface IntakeVersionDetail extends IntakeVersion {
  template_id: string
  items: IntakeItem[]
}

interface Assignment {
  id: string
  version_id: string
  status: string
  receipt_code: string | null
  progress: { complete: boolean; missing: string[] }
}

interface ReviewItem {
  id: string
  key: string
  item_type: string
  value: Record<string, unknown> | null
  provenance: string | null
  superseded_count: number
}

interface ReviewSignature {
  id: string
  item_id: string
  document_version_id: string
  document_digest: string
  signer_role: string
  signer_typed_name: string
  auth_strength: string
}

interface Review extends Assignment {
  packet_name: string
  version: number
  items: ReviewItem[]
  signatures: ReviewSignature[]
  events: { kind: string; note_to_patient: string | null }[]
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
}

// --- the words on the form -------------------------------------------------

const CONSENT_TITLE = "Consent to treatment"
const CONSENT_SENTENCE = "I agree to begin treatment at this practice."
const CONSENT_CLAUSE = "Either of us may end treatment at any time."
const CONSENT_BODY = `${CONSENT_SENTENCE}\n\n- ${CONSENT_CLAUSE}`

const CONSENT_QUESTION = "Before we start"
const CARD_QUESTION = "A photo of your insurance card"
const RECORDS_QUESTION = "Anything a previous provider sent you"
const HEARD_QUESTION = "How did you hear about this practice?"
const KIN_QUESTION = "Who should we call in an emergency?"

const FIRST_ANSWER = "Panic before every shift."
const REDONE_ANSWER = "Panic before every shift, since about March."
const CORRECTION_NOTE = "Could you say a bit more about when this started?"
const SIGNED_NAME = "Ada Lovelace"

const PAYER_NAME = "Blue Sky Mutual"
const MEMBER_ID = "BSM4429100"

/** Who the practice writes down for somebody sitting in the room. */
const NEXT_OF_KIN = {
  name: "Mary Somerville",
  phone: "+15025551212",
  relationship: "Sister",
}

/**
 * How many questions on this packet collect something.
 *
 * All nine. Eight of them are required, which is a different count and the
 * one the list of outstanding questions reports: the emergency contact is
 * optional here, so nobody is held up by it and the practice writes it down
 * itself further below.
 */
const COUNTED = 9

/** A phone, because that is where most people meet a form like this. */
const PHONE = { width: 390, height: 844 }

/** What the suite's other specs run at, restored before a chart is read. */
const DESK = { width: 1280, height: 720 }

// --- building the packet ---------------------------------------------------

/** Write a consent document and publish it, through the practice's own API. */
async function publishDocument(api: ApiClient): Promise<IntakeDocument> {
  const draft = await api.post<IntakeDocument>("/api/intake/documents", {
    title: `${CONSENT_TITLE} ${Date.now().toString(36)}`,
    body_markdown: CONSENT_BODY,
    signer_roles: ["patient"],
  })
  const published = await api.post<IntakeDocument>(`/api/intake/documents/${draft.id}/publish`)
  expect(published.published_at, "a document is only ever pointed at frozen").not.toBeNull()
  return published
}

/**
 * The packet this journey is about, published.
 *
 * Through the builder's API rather than the settings screens, which the test
 * above this one drives instead. Two reasons to keep them apart: a failure
 * in the editor should not read as a failure in the journey, and the journey
 * needs the item ids the publish hands back, which a browser would then have
 * to go looking for.
 *
 * Nine questions of eight kinds. The emergency contact is the one marked
 * optional, so the walk can leave it for the practice to write down in the
 * room — which is what gives the clinician-entry route something to answer
 * that nobody has answered before it.
 */
async function publishPacket(api: ApiClient, documentKey: string): Promise<IntakeVersionDetail> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Before your first session ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        { key: "identity", item_type: "demographics", config: {} },
        { key: "reason", item_type: "reason", config: {} },
        { key: "phq9", item_type: "instrument", config: { code: "phq9" } },
        { key: "gad7", item_type: "instrument", config: { code: "gad7" } },
        {
          key: "consent",
          item_type: "consent_document",
          label: CONSENT_QUESTION,
          config: { document_key: documentKey },
        },
        {
          key: "card",
          item_type: "insurance_card",
          label: CARD_QUESTION,
          config: { sides: "both", collect_fields: true },
        },
        {
          key: "records",
          item_type: "document_request",
          label: RECORDS_QUESTION,
          config: {},
        },
        {
          key: "heard",
          item_type: "single_choice",
          label: HEARD_QUESTION,
          config: {
            options: [
              { key: "gp", label: "My doctor" },
              { key: "search", label: "I searched for one" },
            ],
          },
        },
        {
          key: "kin",
          item_type: "emergency_contact",
          label: KIN_QUESTION,
          required: false,
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

/** A form whose only question asks for a photograph of a card. */
async function publishCardForm(api: ApiClient): Promise<IntakeVersionDetail> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Your card ${Date.now().toString(36)}`,
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
      ],
    },
  )
  return api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
}

/** A form whose only question is one document to sign. */
async function publishConsentForm(
  api: ApiClient,
  documentKey: string,
  resignOnNewVersion: boolean,
): Promise<IntakeVersionDetail> {
  const template = await api.post<IntakeTemplate>("/api/intake/templates", {
    name: `Paperwork ${Date.now().toString(36)}`,
  })
  const draftId = template.versions[0].id

  await api.put<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/items`,
    {
      items: [
        {
          key: "consent",
          item_type: "consent_document",
          label: CONSENT_QUESTION,
          resign_on_new_version: resignOnNewVersion,
          config: { document_key: documentKey },
        },
      ],
    },
  )
  return api.post<IntakeVersionDetail>(
    `/api/intake/templates/${template.id}/versions/${draftId}/publish`,
  )
}

/** One question off a published version, by the key it was published under. */
function itemOf(version: IntakeVersionDetail, key: string): IntakeItem {
  const found = version.items.find((item) => item.key === key)
  expect(found, `the published form carries ${key}`).toBeDefined()
  return found as IntakeItem
}

// --- inviting the same person twice ----------------------------------------

/**
 * Wait for one more of something than was already there.
 *
 * `mail.waitFor` reads the newest message to an address, which is the right
 * answer the first time and a race the second: inviting somebody who has
 * been invited before is exactly when the newest message is still the
 * previous invitation, and redeeming a spent token fails as a bad code. The
 * journey below invites twice, so it counts first and waits for one more.
 */
async function oneMore<T>(read: () => Promise<T[]>, already: number, what: string): Promise<T> {
  const deadline = Date.now() + 15_000
  for (;;) {
    const all = await read()
    if (all.length > already) return all[all.length - 1]
    if (Date.now() >= deadline) throw new Error(`no new ${what} within 15s`)
    await new Promise((resolve) => setTimeout(resolve, 250))
  }
}

/**
 * What the practice calls the form this version belongs to.
 *
 * The picker lists forms by name, and the name is on the template rather
 * than on the version — so a spec that publishes a version and then wants to
 * choose it on a screen has to ask. Named rather than taken first, because
 * the practice this runs against keeps every form a sibling spec published.
 */
async function templateName(api: ApiClient, templateId: string): Promise<string> {
  const templates = await api.get<IntakeTemplate[]>("/api/intake/templates")
  const mine = templates.find((template) => template.id === templateId)
  expect(mine, `the practice still has template ${templateId}`).toBeTruthy()
  return (mine as IntakeTemplate).name
}

/**
 * Read back the two factors of whichever invitation `send` causes.
 *
 * Counted before and after rather than drained, because the mail server and
 * the text gateway are shared: what makes this invitation THIS one is that
 * it arrived after the send, not that the box was empty first.
 *
 * Taking the send as an argument is what lets a screen prove it: the route
 * and the button reach the same place, and a test that always POSTs cannot
 * tell whether the button does.
 */
async function invitationFrom(
  email: string,
  phone: string,
  send: () => Promise<void>,
): Promise<PortalInvitation> {
  const letters = async () => (await mail.received()).filter((m) => m.to.includes(email))
  const texts = async () => (await sms.received()).filter((m) => m.to === phone)
  const sentLetters = (await letters()).length
  const sentTexts = (await texts()).length

  await send()

  const link = firstLink(await oneMore(letters, sentLetters, `mail for ${email}`))
  const token = new URLSearchParams(new URL(link).hash.slice(1)).get("invite")
  expect(token, `the invitation email carries a token: ${link}`).toBeTruthy()
  const otp = stepUpCode(await oneMore(texts, sentTexts, `text for ${phone}`))
  return { link, token: token as string, otp }
}

/** Invite the patient through the route, and read back what it sent. */
async function nextInvitation(
  api: ApiClient,
  patientId: string,
  email: string,
  phone: string,
): Promise<PortalInvitation> {
  return invitationFrom(email, phone, async () => {
    await api.post(`/api/patients/${patientId}/portal-invite`)
  })
}

/**
 * Open an invitation link in a page that is already on that link's page.
 *
 * Two invitations to one practice differ only in the URL fragment, and a
 * navigation that changes only the fragment does not reload — so the shell
 * would never re-read the page it is standing on. Going somewhere else
 * first is what makes the second sign-in a real one.
 */
async function signInAgain(page: Page, invitation: PortalInvitation): Promise<void> {
  await page.goto("about:blank")
  await signInToPortal(page, invitation)
}

// --- reading the file back -------------------------------------------------

interface ExportedFile {
  status: number
  body: string
  disposition: string | undefined
  type: string | undefined
}

/**
 * Fetch the document with the clinician's own bearer token.
 *
 * The shared API client parses what it gets as JSON and this route sends a
 * document, so the request is made directly — which is also the only way to
 * assert on the bytes and on the headers that make a browser save them.
 */
async function exportTheForm(
  request: APIRequestContext,
  idToken: string,
  url: string,
): Promise<ExportedFile> {
  const response = await request.get(url, {
    headers: { Authorization: `Bearer ${idToken}` },
    failOnStatusCode: false,
  })
  return {
    status: response.status(),
    body: await response.text(),
    disposition: response.headers()["content-disposition"],
    type: response.headers()["content-type"],
  }
}

/**
 * The document without the one line two copies are allowed to differ in.
 *
 * "Exported on" carries the moment the copy was taken, which is the whole
 * reason the rest of the file is comparable against a copy somebody else
 * was sent.
 */
function withoutTheExportMoment(body: string): string {
  return body
    .split("\n")
    .filter((line) => !line.includes("Exported on "))
    .join("\n")
}

/** Send a file as the patient does, returning what finalize answered. */
async function finalizeAsPatient(
  request: APIRequestContext,
  sessionToken: string,
  file: UploadFile,
): Promise<number> {
  const headers = { Authorization: `Bearer ${sessionToken}` }
  const started = await request.post(`${BACKEND_URL}/api/patient/documents/init`, {
    headers,
    data: {
      filename: file.name,
      mime_type: file.mimeType,
      size_bytes: file.body.length,
      category: "intake_artifact",
    },
  })
  expect(started.status(), `the API mints an upload target for ${file.name}`).toBe(201)
  const init = (await started.json()) as { document_id: string; upload: UploadTarget }

  const stored = await sendToUploadTarget(request, init.upload, file)
  expect(stored, `the store takes the bytes for ${file.name}`).toBeLessThan(300)

  const finalized = await request.post(
    `${BACKEND_URL}/api/patient/documents/${init.document_id}/finalize`,
    { headers, failOnStatusCode: false },
  )
  return finalized.status()
}

// ---------------------------------------------------------------------------

test.describe("intake, assignment through accepted export", () => {
  /**
   * The form builder, in a browser.
   *
   * Nothing else in this suite opens it, so this is the only place that says
   * the screens a practice actually builds a form on produce a form the rest
   * of the engine accepts. Every assertion at the end is against what the
   * PUBLISH stored, because publishing is what freezes a version and what
   * refuses one that does not hold together — a screen that looked right and
   * published something else would satisfy every assertion made on the screen.
   */
  test("a practice builds a packet on its own settings screens and publishes it @portal", async ({
    api,
    page,
  }) => {
    const document = await publishDocument(api)
    const before = (await api.get<IntakeTemplate[]>("/api/intake/templates")).map((t) => t.id)

    await page.setViewportSize(DESK)
    await page.goto("/dashboard/settings/portal")

    // Everything below is scoped to the Forms card: the documents card and
    // the licensed-instruments card are on this page too, and all three have
    // a "Name" field and a "Save" button.
    const forms = page.getByRole("region", { name: "Forms" })
    await expect(forms).toBeVisible()

    // Every form this card creates is called "New form" and there is no
    // rename on this screen, so the one just added is the last one.
    await forms.getByRole("button", { name: "Add a form" }).click()
    const added = forms.getByRole("button", { name: "New form" }).last()
    await expect(added).toBeVisible()
    await added.click()

    /** Add one question of this kind. The editor opens it for us. */
    const addQuestion = async (kind: string) => {
      await forms.getByRole("combobox", { name: "Kind of question" }).click()
      await page.getByRole("option", { name: kind, exact: true }).click()
      await forms.getByRole("button", { name: "Add question" }).click()
    }

    /** Pick a value in one of the open question's own dropdowns. */
    const pick = async (field: string, value: string) => {
      await forms.getByRole("combobox", { name: field }).click()
      await page.getByRole("option", { name: value, exact: true }).click()
    }

    /** The question box on whichever item the editor has open. */
    const questionBox = () => forms.getByLabel("Question", { exact: true })

    // The two the engine words itself. Nothing to type: the question is
    // Pablo's, and the box above it is an override a practice leaves empty.
    await addQuestion("Name and date of birth")
    await addQuestion("What brings you in")

    // A measure. Only the ones this practice may ask are offered, and the
    // wording comes from the engine rather than from anything typed here.
    await addQuestion("Measure")
    await pick("Which measure", "PHQ-9")

    // A document to read and sign. The picker offers published documents,
    // and which WORDING gets signed is settled by publishing this form.
    await addQuestion("Consent to sign")
    await questionBox().fill(CONSENT_QUESTION)
    await pick("Which document", document.title)

    // A card, and the plan in words as well as in a photograph.
    //
    // One photograph rather than two, because two is what the picker already
    // shows: choosing the value a control is resting on changes nothing and
    // writes nothing, so it would prove nothing about the picker either.
    await addQuestion("Insurance card")
    await questionBox().fill(CARD_QUESTION)
    await pick("How many photos", "Front only")
    await forms.getByLabel("Also ask them to type the plan details").check()

    // Anything else the practice needs back.
    await addQuestion("Document upload")
    await questionBox().fill(RECORDS_QUESTION)

    // A question the practice wrote, with answers it wrote.
    await addQuestion("Pick one")
    await questionBox().fill(HEARD_QUESTION)
    await forms.getByRole("button", { name: "Add an answer" }).click()
    await forms.getByRole("button", { name: "Add an answer" }).click()
    // By role, not by label: each answer's box and the button that removes
    // it are both labelled for the same answer, so a label alone matches two.
    await forms.getByRole("textbox", { name: "Answer 1", exact: true }).fill("My doctor")
    await forms
      .getByRole("textbox", { name: "Answer 2", exact: true })
      .fill("I searched for one")

    // The one marked optional, so a form can still be handed in without it.
    // The practice writes this one down in the room.
    await addQuestion("Emergency contact")
    await questionBox().fill(KIN_QUESTION)
    const mustAnswer = forms.getByRole("switch", { name: "emergency_contact has to be answered" })
    await expect(mustAnswer).toHaveAttribute("aria-checked", "true")
    await mustAnswer.click()
    await expect(mustAnswer).toHaveAttribute("aria-checked", "false")

    // And one asked only of the people it applies to. The picker offers the
    // questions ABOVE this one, which is the whole of the rule that a
    // condition may only look backwards.
    await addQuestion("Written answer")
    await questionBox().fill("Who was that?")
    await forms.getByLabel("Longest answer, in characters").fill("500")
    await pick("Based on", HEARD_QUESTION)
    await pick("When they", "picked")
    await pick("Value", "My doctor")

    const save = forms.getByRole("button", { name: "Save", exact: true })
    await save.click()
    await expect(save).toBeEnabled()

    // The draft is on the server before the publish is asked for, so a
    // publish that raced the save is not what this test could be proving.
    const templates = await api.get<IntakeTemplate[]>("/api/intake/templates")
    const mine = templates.find((t) => !before.includes(t.id))
    expect(mine, "the card created a form").toBeDefined()
    const template = mine as IntakeTemplate
    const draftUrl = `/api/intake/templates/${template.id}/versions/${template.versions[0].id}`
    await expect
      .poll(async () => (await api.get<IntakeVersionDetail>(draftUrl)).items.length, {
        message: "the editor saved every question before publishing",
      })
      .toBe(9)

    await forms.getByRole("button", { name: "Publish" }).click()

    // The screen says what a published version is: not an error, and not a
    // form to keep typing into.
    await expect(forms).toContainText("This version has been published")

    // --- and what was stored is what was typed ------------------------------
    const published = await api.get<IntakeVersionDetail>(draftUrl)
    expect(published.published_at, "publishing freezes the version").not.toBeNull()
    expect(published.items.map((item) => item.item_type)).toEqual([
      "demographics",
      "reason",
      "instrument",
      "consent_document",
      "insurance_card",
      "document_request",
      "single_choice",
      "emergency_contact",
      "free_text",
    ])

    expect(published.items[2].config.code, "the measure the practice chose").toBe("phq9")

    // Publishing is what pins WHICH wording a patient will sign, so the
    // document can be revised afterwards without touching the form.
    const consent = published.items[3]
    expect(consent.config.document_key).toBe(document.document_key)
    expect(consent.config.document_version_id, "the wording is pinned at publish").toBe(document.id)

    const card = published.items[4]
    expect(card.label).toBe(CARD_QUESTION)
    expect(card.config.sides, "the practice narrowed it to one photograph").toBe("front")
    expect(card.config.collect_fields).toBe(true)

    const heard = published.items[6]
    expect(heard.label).toBe(HEARD_QUESTION)
    const options = heard.config.options as { key: string; label: string }[]
    expect(options.map((option) => option.label)).toEqual(["My doctor", "I searched for one"])

    const kin = published.items[7]
    expect(kin.label).toBe(KIN_QUESTION)
    expect(kin.required, "the practice turned this one off").toBe(false)

    // The rule points at the earlier question by its stored key and compares
    // against the ANSWER's key rather than its label — so relabelling either
    // one later cannot silently repoint the branch.
    const branched = published.items[8]
    expect(branched.config.max_len).toBe(500)
    expect(branched.config.visible_when).toEqual({
      item_key: heard.key,
      op: "eq",
      value: options[0].key,
    })
  })

  /**
   * The journey, on the phone most people meet it on.
   *
   * Long on purpose. Every step here is a step the one before it made
   * possible, and the failures worth catching live in the joins: a form that
   * resumes at the wrong question, a card that counts as answered before a
   * photograph arrives, a correction that reopens more than was asked about,
   * an export that prints an answer the patient replaced as though it still
   * stood.
   */
  test("a patient completes a packet on a phone and the practice accepts and files it @portal", async ({
    api,
    onboardedUser,
    page,
    request,
  }) => {
    // Two sign-ins, five uploads and a walk through nine screens.
    test.slow()

    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone, date_of_birth: "1990-06-21" })
    const document = await publishDocument(api)
    const version = await publishPacket(api, document.document_key)

    const identity = itemOf(version, "identity")
    const reason = itemOf(version, "reason")
    const phq9 = itemOf(version, "phq9")
    const gad7 = itemOf(version, "gad7")
    const consent = itemOf(version, "consent")
    const card = itemOf(version, "card")
    const records = itemOf(version, "records")
    const heard = itemOf(version, "heard")
    const kin = itemOf(version, "kin")

    // --- the practice sends it, from the chart ------------------------------
    // Both halves of starting an intake are one press here: the form goes out
    // and, because this patient has no way in yet, so does an invitation.
    // Driving it from the screen is the point — the routes underneath are
    // proven either way, and what a clinician cannot do is POST.
    const packetName = await templateName(api, version.template_id)

    await page.goto(`/dashboard/patients/${patient.id}`)
    await page.getByRole("combobox", { name: "Form" }).click()
    await page.getByRole("option", { name: packetName }).click()

    const invitation = await invitationFrom(email, phone, async () => {
      await page.getByTestId("send-intake-form-button").click()
      await expect(page.getByTestId("send-intake-form-sent")).toContainText(
        "link by email and a code by text",
      )
    })

    // The form the press sent is on the chart, named, before anybody answers it.
    await expect(page.getByTestId("intake-assignments")).toContainText(packetName)

    const assigned = (
      await api.get<Assignment[]>(`/api/patients/${patient.id}/intake-assignments`)
    )[0]
    expect(assigned.version_id, "the version the picker offered").toBe(version.id)
    const chart = `/api/patients/${patient.id}/intake-assignments/${assigned.id}`

    // Eight of the nine are outstanding, and the server is what says so —
    // the optional one it never asks for is not among them.
    expect(assigned.progress.complete).toBe(false)
    expect(assigned.progress.missing).toEqual([
      identity.id,
      reason.id,
      phq9.id,
      gad7.id,
      consent.id,
      card.id,
      records.id,
      heard.id,
    ])

    // --- the invitation arrives, and is met on a phone ----------------------
    await page.setViewportSize(PHONE)
    await signInToPortal(page, invitation)

    await expect(page.getByTestId("forms-list-state")).toContainText("8 questions left")
    await page.getByTestId("forms-list-open").click()

    // Who you are: the chart's own name, for the patient to confirm.
    await expect(page.getByTestId("forms-progress")).toContainText(`Question 1 of ${COUNTED}`)
    await expect(page.getByTestId("forms-identity-name")).toContainText(patient.first_name)
    await page.getByTestId("forms-identity-confirm").click()
    await page.getByTestId("forms-continue").click()

    // What brings you in.
    await expect(page.getByTestId("forms-progress")).toContainText(`Question 2 of ${COUNTED}`)
    await page.getByTestId("forms-reason").fill(FIRST_ANSWER)
    await page.getByTestId("forms-continue").click()

    // --- put down halfway, and signed out -----------------------------------
    // Not a reload: signing out ends the session and forgets it, so what
    // comes back cannot have come from anything this browser was holding.
    await expect(page.getByTestId("forms-progress")).toContainText(`Question 3 of ${COUNTED}`)
    await page.getByTestId("portal-shell-sign-out").click()
    await expect(page.getByTestId("portal-shell-active")).toHaveCount(0)

    // --- and picked up again on a new invitation ----------------------------
    await signInAgain(page, await nextInvitation(api, patient.id, email, phone))
    await expect(page.getByTestId("forms-list-state")).toContainText("6 questions left")
    await page.getByTestId("forms-list-open").click()

    // Where it resumes is the server's answer: the first question it still
    // calls outstanding, not the furthest screen anybody reached.
    await expect(page.getByTestId("forms-progress")).toContainText(`Question 3 of ${COUNTED}`)

    // The first measure, and the crisis line that rides every one of them.
    await expect(page.getByTestId("forms-crisis-footer")).toContainText("988")
    await answerMeasureOnScreen(page)
    await page.getByTestId("forms-continue").click()

    // The second.
    await expect(page.getByTestId("forms-progress")).toContainText(`Question 4 of ${COUNTED}`)
    await expect(page.getByTestId("forms-crisis-footer")).toContainText("988")
    await answerMeasureOnScreen(page)
    await page.getByTestId("forms-continue").click()

    // --- the document, read and signed --------------------------------------
    await expect(page.getByTestId("forms-progress")).toContainText(`Question 5 of ${COUNTED}`)
    // The words the practice wrote reach the person being asked to agree,
    // including the bullet, which only the server's renderer produces.
    await expect(page.getByTestId("forms-consent-document")).toContainText(CONSENT_SENTENCE)
    await expect(page.getByTestId("forms-consent-document")).toContainText(CONSENT_CLAUSE)
    await expect(page.getByTestId("forms-consent-statement")).toContainText("electronic signature")

    // Sign is not offered until both halves are done.
    await expect(page.getByTestId("forms-consent-sign")).toBeDisabled()
    await page.getByTestId("forms-consent-affirm").check()
    await expect(page.getByTestId("forms-consent-sign")).toBeDisabled()
    await page.getByTestId("forms-consent-name").fill(SIGNED_NAME)
    await page.getByTestId("forms-consent-sign").click()
    await expect(page.getByTestId("forms-consent-signed")).toContainText(SIGNED_NAME)
    await page.getByTestId("forms-continue").click()

    // --- the two that ask for a file, walked past without one ---------------
    // Deliberately: what the form does when it is handed in early is the
    // thing worth proving, and it can only be proven from here.
    await expect(page.getByRole("heading", { name: CARD_QUESTION })).toBeVisible()
    await expect(page.getByTestId("forms-upload-front")).toBeVisible()
    await expect(page.getByTestId("forms-upload-back")).toBeVisible()
    await expect(page.getByTestId("forms-coverage-fields")).toBeVisible()
    await page.getByTestId("forms-continue").click()

    await expect(page.getByRole("heading", { name: RECORDS_QUESTION })).toBeVisible()
    await expect(page.getByTestId("forms-upload-choose")).toHaveCount(1)
    await page.getByTestId("forms-continue").click()

    // A question the practice wrote, answered in its own words.
    await expect(page.getByRole("heading", { name: HEARD_QUESTION })).toBeVisible()
    await page.getByTestId("forms-single-choice").getByRole("radio", { name: "My doctor" }).check()
    await page.getByTestId("forms-continue").click()

    // The last one, and the only optional one. It is asked properly, and
    // leaving it blank does not hold the form up — which is what lets the
    // practice write this one down in the room further below.
    await expect(page.getByRole("heading", { name: KIN_QUESTION })).toBeVisible()
    await expect(page.getByTestId("forms-progress")).toContainText(`Question 9 of ${COUNTED}`)
    await expect(page.getByTestId("forms-contact-name")).toBeEmpty()
    await expect(page.getByTestId("forms-contact-relationship")).toBeVisible()
    await expect(page.getByTestId("forms-contact-phone")).toBeVisible()
    await page.getByTestId("forms-continue").click()

    // --- handed in early, and refused ---------------------------------------
    await expect(page.getByTestId("forms-review")).toBeVisible()
    await expect(page.getByTestId("forms-review")).toContainText(FIRST_ANSWER)
    await page.getByTestId("forms-submit").click()

    // The refusal is the server's, in the server's own words, and the form
    // stays open. Nothing in the browser decided this — sending is offered
    // whatever the answers look like.
    await expect(page.getByTestId("forms-submit-error")).toContainText(
      "Some questions still need an answer",
    )
    await expect(page.getByTestId("forms-receipt-code")).toHaveCount(0)

    // And it names which ones: the two nobody has sent a file for.
    const early = await api.get<Assignment>(chart)
    expect(early.status).not.toBe("submitted")
    expect(early.progress.missing).toEqual([card.id, records.id])

    // --- the photographs, and the plan printed on the card ------------------
    const front = fixtureFile("insurance-card.png", "image/png")
    const back = fixtureFile("insurance-card-back.png", "image/png")
    const referral = fixtureFile("records.pdf", "application/pdf")

    await page.getByTestId(`forms-review-row-${card.id}`).getByTestId("forms-review-edit").click()
    await page.getByTestId("forms-upload-front-input").setInputFiles(toInputFile(front))
    await expect(page.getByTestId("forms-upload-front-sent")).toBeVisible()
    await page.getByTestId("forms-upload-back-input").setInputFiles(toInputFile(back))
    await expect(page.getByTestId("forms-upload-back-sent")).toBeVisible()

    // These are not the question's answer — the photograph is. They go onto
    // the client's coverage record, which is why they have their own button.
    await page.getByTestId("forms-coverage-payer").fill(PAYER_NAME)
    await page.getByTestId("forms-coverage-member").fill(MEMBER_ID)
    await page.getByTestId("forms-coverage-save").click()
    await expect(page.getByTestId("forms-coverage-saved")).toBeVisible()
    await page.getByTestId("forms-continue").click()

    await expect(page.getByRole("heading", { name: RECORDS_QUESTION })).toBeVisible()
    await page.getByTestId("forms-upload-input").setInputFiles(toInputFile(referral))
    await expect(page.getByTestId("forms-upload-sent")).toBeVisible()
    await page.getByTestId("forms-continue").click()

    // Back out through the last two screens to the review.
    await page.getByTestId("forms-continue").click()
    await page.getByTestId("forms-continue").click()
    await expect(page.getByTestId("forms-review")).toBeVisible()

    // Completion flips, and the server is what says so.
    const ready = await api.get<Assignment>(chart)
    expect(ready.progress.complete).toBe(true)
    expect(ready.progress.missing).toEqual([])

    // --- and it goes in ------------------------------------------------------
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toHaveText(/^[2-9A-HJ-NP-TV-Z]{8}$/)
    await expect(page.getByTestId("forms-crisis-footer")).toContainText("988")

    // No total and no band reaches the person who answered the questions.
    // Matched lower case on purpose: the receipt code is upper case, so a
    // band word cannot be read out of a random eight characters.
    for (const band of ["minimal", "mild", "moderate", "severe"]) {
      await expect(page.getByTestId("forms-receipt")).not.toContainText(band)
    }
    await page.getByTestId("forms-receipt-close").click()

    // --- what the practice has ----------------------------------------------
    const handedIn = await api.get<Review>(`${chart}/review`)
    expect(handedIn.status).toBe("submitted")
    expect(handedIn.receipt_code).toMatch(/^[2-9A-HJ-NP-TV-Z]{8}$/)
    // The exact version, so the chart names the wording that was answered
    // rather than whatever the form says today.
    expect(handedIn.version).toBe(1)

    const answered = (key: string) =>
      handedIn.items.find((item) => item.key === key) as ReviewItem
    expect(answered("reason").value).toEqual({ text: FIRST_ANSWER })
    expect(answered("reason").provenance).toBe("patient")
    expect(answered("heard").value).toEqual({ key: "gp" })
    expect(answered("kin").value, "nobody has answered this one yet").toBeNull()

    // The signature names the exact revision that was on the screen, and the
    // strength of the session it was taken under.
    expect(handedIn.signatures).toHaveLength(1)
    expect(handedIn.signatures[0].item_id).toBe(consent.id)
    expect(handedIn.signatures[0].document_version_id).toBe(document.id)
    expect(handedIn.signatures[0].document_digest).toBe(document.digest)
    expect(handedIn.signatures[0].signer_typed_name).toBe(SIGNED_NAME)
    expect(handedIn.signatures[0].auth_strength).toBe("stepped_up")

    // --- one question goes back ----------------------------------------------
    const sentBack = await api.post<Assignment>(`${chart}/request-correction`, {
      item_ids: [reason.id],
      note: CORRECTION_NOTE,
    })
    expect(sentBack.status).toBe("needs_correction")

    await page.reload()
    await expect(page.getByTestId("forms-list-state")).toContainText("Your practice")
    await page.getByTestId("forms-list-open").click()

    // The one question they asked about, and nothing else: the rest of the
    // form was finished when it went in.
    await expect(page.getByTestId("forms-reason")).toHaveValue(FIRST_ANSWER)
    await expect(page.getByTestId("forms-progress")).toContainText("Question 1 of 1")
    await expect(page.getByTestId("forms-identity-name")).toHaveCount(0)

    await page.getByTestId("forms-reason").fill(REDONE_ANSWER)
    await page.getByTestId("forms-continue").click()

    // Their clinician's own words, on the screen the patient sends from.
    await expect(page.getByTestId("forms-correction-note")).toContainText(CORRECTION_NOTE)
    await page.getByTestId("forms-submit").click()
    await expect(page.getByTestId("forms-receipt-code")).toBeVisible()

    // --- the practice writes down the one nobody answered -------------------
    await api.post<Assignment>(`${chart}/items/${kin.id}/clinician-entry`, {
      value: NEXT_OF_KIN,
    })
    const accepted = await api.post<Assignment>(`${chart}/accept`, {})
    expect(accepted.status).toBe("accepted")

    const closed = await api.get<Review>(`${chart}/review`)
    const settled = (key: string) => closed.items.find((item) => item.key === key) as ReviewItem
    expect(settled("reason").value).toEqual({ text: REDONE_ANSWER })
    expect(settled("reason").superseded_count, "the answer it replaced is kept").toBe(1)
    expect(settled("kin").value).toEqual(NEXT_OF_KIN)
    expect(settled("kin").provenance).toBe("clinician")
    expect(closed.events.map((event) => event.kind)).toEqual([
      "correction_requested",
      "corrected",
      "clinician_entered",
      "accepted",
    ])

    // --- the chart, as a clinician reads it ----------------------------------
    await page.setViewportSize(DESK)
    await page.goto(`/dashboard/patients/${patient.id}`)
    await expect(page.getByTestId("intake-card")).toBeVisible()
    await expect(page.getByTestId("intake-artifacts")).toBeVisible()
    await expect(page.getByTestId("intake-artifact")).toHaveCount(3)
    await expect(page.getByTestId("intake-artifacts")).toContainText(front.name)
    await expect(page.getByTestId("intake-artifacts")).toContainText(back.name)
    await expect(page.getByTestId("intake-artifacts")).toContainText(referral.name)

    // The plan read off the photographed card sits with the photographs,
    // with the member id shown by its last four and no more.
    await expect(page.getByTestId("intake-coverage")).toContainText(PAYER_NAME)
    await expect(page.getByTestId("intake-coverage")).not.toContainText(MEMBER_ID)
    await expect(page.getByTestId("intake-coverage")).toContainText(MEMBER_ID.slice(-4))

    // --- and the files are the files the patient sent ------------------------
    const listed = await api.get<ChartArtifact[]>(`${chart}/artifacts`)
    expect(listed.map((row) => row.side)).toEqual(["front", "back", null])
    expect(listed.map((row) => row.item_label)).toEqual([
      CARD_QUESTION,
      CARD_QUESTION,
      RECORDS_QUESTION,
    ])
    // Every one, not just the first: the three are different bytes, so a row
    // paired with the wrong object passes a count and fails a hash.
    for (const [row, sent] of [
      [listed[0], front],
      [listed[1], back],
      [listed[2], referral],
    ] as const) {
      const link = await api.get<{ url: string }>(`/api/documents/${row.document_id}/file`)
      const fetched = await request.get(link.url)
      expect(fetched.status(), `the practice can fetch ${sent.name}`).toBe(200)
      expect(sha256(await fetched.body()), `${sent.name} survived the round trip`).toBe(
        sha256(sent.body),
      )
    }

    // --- the file a practice keeps -------------------------------------------
    const receipt = closed.receipt_code as string
    const idToken = await signInWithPassword(onboardedUser.email, onboardedUser.password)
    const file = await exportTheForm(request, idToken, `${api.baseUrl}${chart}/export`)
    expect(file.status, `the export is served (${file.status})`).toBe(200)
    expect(file.disposition).toBe(`attachment; filename="intake-${receipt}.html"`)
    expect(file.type).toContain("text/html")

    // When the copy was taken, and what it is a copy of.
    expect(file.body).toContain("Exported on ")
    expect(file.body).toContain(receipt)
    expect(file.body).toContain("Accepted.")

    // The answer that stands, the one it replaced, and who wrote each.
    expect(file.body).toContain(REDONE_ANSWER)
    expect(file.body).toContain(FIRST_ANSWER)
    expect(file.body).toContain("Replaced")
    expect(file.body).toContain("Answered by the patient")
    expect(file.body).toContain("Entered by the practice")
    expect(file.body).toContain(NEXT_OF_KIN.name)

    // Both measures, scored the same way every time. Every item answered at
    // its first anchor, which is nought, and the band that goes with it.
    expect(file.body).toContain("PHQ-9: 0 of 27 (minimal)")
    expect(file.body).toContain("GAD-7: 0 of 21 (minimal)")

    // What was signed, what was asked for, and what arrived.
    expect(file.body).toContain("Signatures")
    expect(file.body).toContain(SIGNED_NAME)
    expect(file.body).toContain("Attached files")
    expect(file.body).toContain(`${front.name} (front)`)
    expect(file.body).toContain(`${back.name} (back)`)
    expect(file.body).toContain(referral.name)
    expect(file.body).toContain("History")
    expect(file.body).toContain(CORRECTION_NOTE)

    // Self-contained: nothing to run.
    expect(file.body.toLowerCase()).not.toContain("<script")

    // The same form, the same bytes, apart from the line that says when the
    // copy was taken — which is what makes one copy checkable against
    // another somebody else was sent.
    const again = await exportTheForm(request, idToken, `${api.baseUrl}${chart}/export`)
    expect(withoutTheExportMoment(again.body)).toBe(withoutTheExportMoment(file.body))

    // A form on somebody else's chart is not found, so the path cannot be
    // used to find out whose form an id names.
    const stranger = await givePatient(api, givePortalContactDetails())
    const elsewhere = await exportTheForm(
      request,
      idToken,
      `${api.baseUrl}/api/patients/${stranger.id}/intake-assignments/${assigned.id}/export`,
    )
    expect(elsewhere.status).toBe(404)
  })

  /**
   * Who may read a form, and who may not.
   *
   * Both refusals are asserted after the control that makes them mean
   * something. "A second patient cannot read this form" is true of a form
   * that does not exist, and "a patient token is refused here" is true of a
   * route that is broken — so each one is preceded by the same request
   * succeeding for the principal it belongs to.
   *
   * Driven at the routes rather than through screens because no screen
   * offers either: the refusals have to hold against something that is not
   * a browser, which is exactly what an attacker brings.
   */
  test("another patient's form, and a clinician route met with a patient session, are refused @portal", async ({
    api,
    onboardedUser,
    request,
  }) => {
    const owner = givePortalContactDetails()
    const ownerPatient = await givePatient(api, { email: owner.email, phone: owner.phone })
    const document = await publishDocument(api)
    const version = await publishPacket(api, document.document_key)
    const assigned = await api.post<Assignment>(
      `/api/patients/${ownerPatient.id}/intake-assignments`,
      { version_id: version.id },
    )

    const ownerSession = await givePortalSession(
      api,
      request,
      ownerPatient.id,
      owner.email,
      owner.phone,
    )
    const form = `${BACKEND_URL}/api/patient/intake/assignments/${assigned.id}`

    // Control: the person the form was sent to can read it.
    const own = await request.get(form, {
      headers: { Authorization: `Bearer ${ownerSession}` },
      failOnStatusCode: false,
    })
    expect(own.status(), "the form is readable by the patient it belongs to").toBe(200)

    // A second patient, signed in properly, with somebody else's form id.
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
    const reached = await request.get(form, {
      headers: { Authorization: `Bearer ${strangerSession}` },
      failOnStatusCode: false,
    })
    // 404, not 403: the id names a form, and the answer must not say whose.
    expect(reached.status(), "another patient's form is not reachable").toBe(404)

    // --- and the other direction --------------------------------------------
    const review = `${BACKEND_URL}/api/patients/${ownerPatient.id}/intake-assignments/${assigned.id}/review`
    const idToken = await signInWithPassword(onboardedUser.email, onboardedUser.password)

    // Control: the clinician whose chart it is can read the review.
    const asClinician = await request.get(review, {
      headers: { Authorization: `Bearer ${idToken}` },
      failOnStatusCode: false,
    })
    expect(asClinician.status(), "the chart is readable by the practice").toBe(200)

    // The patient's own session is a credential for the patient surface and
    // nothing else. It is refused before the route reads anything.
    const asPatient = await request.get(review, {
      headers: { Authorization: `Bearer ${ownerSession}` },
      failOnStatusCode: false,
    })
    expect(asPatient.status(), "a patient session is not a clinician").toBe(401)
  })

  /**
   * A file that is not the kind of file it claims to be.
   *
   * The picker's filter, the signed URL and the storage service all wave it
   * through: it is named `.png`, it is declared `image/png`, and the store
   * is not in the business of reading what it is handed. What catches it is
   * the server reading the stored object's opening bytes when the upload is
   * finalized, which is the only place in the chain that looks.
   *
   * Control first, with the same three calls and a real photograph, so the
   * refusal below is about the bytes rather than about the recipe.
   */
  test("a file that is not the kind of file it claims is refused @portal", async ({
    api,
    page,
    request,
  }) => {
    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone, date_of_birth: "1983-02-17" })
    // One question, so opening the form lands on the picker under test.
    const version = await publishCardForm(api)
    const card = itemOf(version, "card")
    const assigned = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: version.id },
    )
    const chart = `/api/patients/${patient.id}/intake-assignments/${assigned.id}`

    const invitation = await nextInvitation(api, patient.id, email, phone)
    await page.setViewportSize(PHONE)
    await signInToPortal(page, invitation)

    // The session the shell is holding, so the route below is met by the
    // only principal that could ever have made this upload.
    const sessionToken = await page.evaluate(() => {
      const key = Object.keys(window.localStorage).find((name) =>
        name.startsWith("pablo-portal-session:"),
      )
      if (key === undefined) return null
      return (JSON.parse(window.localStorage.getItem(key) as string) as { sessionToken: string })
        .sessionToken
    })
    expect(sessionToken, "the shell stores its session per practice slug").toBeTruthy()

    // Control: a real photograph goes all the way in.
    const real = fixtureFile("insurance-card.png", "image/png")
    expect(await finalizeAsPatient(request, sessionToken as string, real)).toBe(200)

    // The same three calls, with bytes that are not a picture of anything.
    // 422, and the engine names the reason rather than the file.
    const pretender: UploadFile = {
      name: "card.png",
      mimeType: "image/png",
      body: Buffer.from("this is not a picture of anything"),
    }
    expect(await finalizeAsPatient(request, sessionToken as string, pretender)).toBe(422)

    // --- and the same refusal at the picker somebody actually uses ----------
    await page.getByTestId("forms-list-open").click()
    await expect(page.getByRole("heading", { name: CARD_QUESTION })).toBeVisible()
    await page.getByTestId("forms-upload-front-input").setInputFiles(toInputFile(pretender))

    await expect(page.getByTestId("forms-upload-front-error")).toBeVisible()
    await expect(page.getByTestId("forms-upload-front-error")).not.toBeEmpty()
    // The slot goes back to asking rather than showing a file as sent.
    await expect(page.getByTestId("forms-upload-front-choose")).toBeVisible()
    await expect(page.getByTestId("forms-upload-front-sent")).toHaveCount(0)

    // And nothing reached the form: the question is as outstanding as it was.
    const untouched = await api.get<Assignment>(chart)
    expect(untouched.progress.missing).toContain(card.id)
  })

  /**
   * A document revised after it was pinned, on a form that says so.
   *
   * `resign_on_new_version` is the item's own rule rather than the
   * document's: the same wording can be asked for with a fresh signature on
   * one form and without one on another. What it buys is that a practice
   * revising its consent cannot end up holding a signature against words
   * nobody was shown.
   *
   * Two forms rather than two copies of one, because there is no such thing
   * as two copies of one: asking for the same version twice hands back the
   * assignment that is already live, so nobody is left holding two of the
   * same form. Both are published before the revision, so both pin the same
   * wording, and the only difference between them is which one was signed
   * while that wording was current.
   */
  test("a document revised after it was pinned asks for a fresh signature @portal", async ({
    api,
    page,
  }) => {
    const { email, phone } = givePortalContactDetails()
    const patient = await givePatient(api, { email, phone, date_of_birth: "1987-02-09" })
    const document = await publishDocument(api)
    // Both published now, so both pin the wording that is current now.
    const signedForm = await publishConsentForm(api, document.document_key, true)
    const staleForm = await publishConsentForm(api, document.document_key, true)

    const first = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: signedForm.id },
    )
    const second = await api.post<Assignment>(
      `/api/patients/${patient.id}/intake-assignments`,
      { version_id: staleForm.id },
    )
    expect(second.id, "two forms, so two forms to fill in").not.toBe(first.id)

    await page.setViewportSize(PHONE)
    await signInToPortal(page, await nextInvitation(api, patient.id, email, phone))

    // --- control: nothing has been revised, so it signs ---------------------
    await page.getByTestId(`forms-list-row-${first.id}`).getByTestId("forms-list-open").click()
    await expect(page.getByTestId("forms-consent-document")).toContainText(CONSENT_SENTENCE)
    await page.getByTestId("forms-consent-affirm").check()
    await page.getByTestId("forms-consent-name").fill(SIGNED_NAME)
    await page.getByTestId("forms-consent-sign").click()
    await expect(page.getByTestId("forms-consent-signed")).toContainText(SIGNED_NAME)

    // The evidence names the revision that was on the screen.
    const signed = await api.get<Review>(
      `/api/patients/${patient.id}/intake-assignments/${first.id}/review`,
    )
    expect(signed.signatures).toHaveLength(1)
    expect(signed.signatures[0].document_version_id).toBe(document.id)
    expect(signed.signatures[0].document_digest).toBe(document.digest)

    // --- the practice revises the wording -----------------------------------
    const revised = await api.post<IntakeDocument>(
      `/api/intake/documents/${document.id}/new-version`,
      {},
    )
    const republished = await api.post<IntakeDocument>(
      `/api/intake/documents/${revised.id}/publish`,
    )
    expect(republished.version, "a revision is a new version of the same document").toBeGreaterThan(
      document.version,
    )

    // --- the second copy still points at the old words, and refuses ---------
    await page.reload()
    await page.getByTestId(`forms-list-row-${second.id}`).getByTestId("forms-list-open").click()
    await page.getByTestId("forms-consent-affirm").check()
    await page.getByTestId("forms-consent-name").fill(SIGNED_NAME)
    await page.getByTestId("forms-consent-sign").click()

    // The patient is told the practice will send the new wording, which is
    // the truthful thing to say: nothing they can do on this screen fixes it.
    await expect(page.getByTestId("forms-consent-resign")).toContainText("newer version")

    // And nothing was recorded against the old words.
    const refused = await api.get<Review>(
      `/api/patients/${patient.id}/intake-assignments/${second.id}/review`,
    )
    expect(refused.signatures, "a stale signature is not recorded").toEqual([])
    expect(refused.progress.complete).toBe(false)
  })
})
