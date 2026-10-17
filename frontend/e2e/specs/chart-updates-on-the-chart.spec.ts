// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Chart updates reviewed from the client's chart, not only from the note.
 *
 * A note imported for a transferring client proposes what its document says
 * about the chart. Opening the client before the next visit shows those
 * proposals at the top of the Chart card, grouped under the note; a whole
 * section is accepted at once; the chart holds the text afterwards and the
 * strip is gone. On the note's own page, the mark beside a proposal's cited
 * paragraph lights that paragraph in the original document.
 *
 * The stack reads imports through its stand-in (scripts/fake_llm.py): the
 * parse relocates each "Label: text" line into the field of that name, and
 * the proposal call answers a numbered "Client: Update on <key>: <text>"
 * paragraph with a proposal for that field citing it.
 */

import { randomBytes } from "node:crypto"
import { readFile } from "node:fs/promises"

import { test, expect } from "../fixtures/auth"
import type { ApiClient } from "../fixtures/api"
import { givePatient } from "../fixtures/scenarios"

type Template = { spec: { label: string } }
type Imported = { id: string; note: { id: string } | null }
type HistoryField = { key: string; text: string | null }
type ChartHistory = { groups: { fields: HistoryField[] }[] }

const TEMPLATE = new URL(
  "../../../backend/app/notes/templates/psychiatric_follow_up.json",
  import.meta.url,
)

async function historyText(api: ApiClient, patientId: string, key: string) {
  const chart = await api.get<ChartHistory>(`/api/patients/${patientId}/chart-history`)
  return chart.groups.flatMap((g) => g.fields).find((f) => f.key === key)?.text
}

test("an imported note's chart updates are reviewed from the client's chart", async ({
  signedInPage: page,
  api,
}) => {
  const template = JSON.parse(await readFile(TEMPLATE, "utf8")) as Template
  const slug = `e2e_strip_${randomBytes(3).toString("hex")}`
  const label = `${template.spec.label} ${slug}`
  await api.request("PUT", `/api/note-types/custom/${slug}`, { ...template.spec, label })

  try {
    const patient = await givePatient(api)
    const document = [
      "Psychiatric medication management follow-up",
      "Chief complaint: Still waking at 4 a.m.",
      "Relationships: Married, two children.",
      "Work school: Teaches fourth grade.",
      "Client: Update on supports: Sister and a neighbor.",
      "Follow up: Return in 6 weeks.",
    ].join("\n\n")

    const form = new FormData()
    form.set("file", new Blob([document], { type: "text/plain" }), "transfer-note.txt")
    form.set("note_type", `custom.${slug}`)
    const imported = await api.postForm<Imported>(
      `/api/patients/${patient.id}/sessions/import`,
      form,
    )

    // The chart's own door: what the note proposed, waiting at the top.
    await page.goto(`/dashboard/patients/${patient.id}`)
    const strip = page.getByRole("region", { name: "Chart updates waiting" })
    await expect(strip).toBeVisible()
    await expect(strip).toContainText("3 chart updates waiting from 1 note")
    await expect(strip).toContainText("imported")
    const social = strip.getByRole("region", { name: "Social history and supports" })
    await expect(social.getByTestId("chart-proposal")).toHaveCount(3)
    await expect(
      social.getByRole("listitem", { name: "Social history and supports: Supports" }),
    ).toContainText("Client: Update on supports: Sister and a neighbor.")

    await social.getByRole("button", { name: "Accept all Social history and supports" }).click()

    // Accepted one after another; each lands on the chart in turn.
    await expect.poll(() => historyText(api, patient.id, "supports")).toBe("Sister and a neighbor.")
    await expect.poll(() => historyText(api, patient.id, "relationships")).toBe("Married, two children.")
    await expect.poll(() => historyText(api, patient.id, "work_school")).toBe("Teaches fourth grade.")
    // Nothing left waiting: the strip goes, the tabs stay.
    await expect(strip).toHaveCount(0)
    await expect(page.getByRole("tab", { name: /Notes/ })).toBeVisible()

    // On the note's page, the decided rows read as such, and a mark lights
    // the paragraph it cites in the original document.
    await page.goto(`/dashboard/sessions/${imported.id}`)
    const updates = page.getByTestId("chart-updates")
    await expect(updates.getByText("Added to the chart")).toHaveCount(3)
    await updates.getByRole("button", { name: "Show source paragraph 5" }).click()
    const paragraph = page.locator('[data-segment-index="4"]')
    await expect(paragraph).toContainText("Client: Update on supports")
    await expect(paragraph).toHaveClass(/ring-1/)
  } finally {
    await api.request("DELETE", `/api/note-types/custom/${slug}`)
  }
})
