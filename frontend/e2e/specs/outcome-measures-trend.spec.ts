// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * A client's PHQ-9 scores, read as a trend on the chart's Measures tab.
 *
 * Two administrations are recorded through the API, the later one first, so
 * the order on screen can only come from the dates the server sorts by and
 * not from the order the rows were written in. The scoring is the server's:
 * the spec sends item responses and checks that the total and severity label
 * the server returned are the ones the chart shows. The earlier score
 * endorses item 9 (thoughts of self-harm), which the chart marks on that
 * administration and on no other.
 */

import type { Locator } from "@playwright/test"

import { test, expect } from "../fixtures/auth"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"
import { givePatient } from "../fixtures/scenarios"

type OutcomeMeasure = {
  id: string
  total_score: number | null
  severity: string | null
  administered_at: string
}

/** Item responses keyed "1".."9", as the instrument registry expects them. */
function phq9(items: number[]): Record<string, number> {
  return Object.fromEntries(items.map((score, i) => [String(i + 1), score]))
}

/** The date as the chart prints it, in the browser's zone. */
function shownDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    timeZone: BROWSER_TIME_ZONE,
  })
}

/** The sparkline's y coordinates, left to right. A higher score is drawn higher, so a smaller y. */
async function sparklineYs(sparkline: Locator): Promise<number[]> {
  const points = (await sparkline.locator("polyline").getAttribute("points")) ?? ""
  return points
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((pair) => Number(pair.split(",")[1]))
}

test("the Measures tab trends a client's PHQ-9 scores in date order, with the server's severity and the item 9 marker", async ({
  api,
  signedInPage: page,
}) => {
  const patient = await givePatient(api)
  const measures = `/api/patients/${patient.id}/outcome-measures`

  // Mid-afternoon UTC, so the date is the same day in the browser's zone.
  const septemberAt = "2026-09-15T16:00:00Z"
  const octoberAt = "2026-10-20T16:00:00Z"

  // The later score first: whatever order the chart shows is the server's.
  const october = await api.post<OutcomeMeasure>(measures, {
    instrument: "phq9",
    source: "manual",
    administered_at: octoberAt,
    item_scores: phq9([1, 1, 1, 1, 1, 1, 1, 0, 0]),
  })
  const september = await api.post<OutcomeMeasure>(measures, {
    instrument: "phq9",
    source: "manual",
    administered_at: septemberAt,
    item_scores: phq9([3, 2, 2, 2, 2, 2, 2, 1, 2]),
  })

  // The server scores them; the chart only shows what it was given.
  expect(september).toMatchObject({ total_score: 18, severity: "moderately severe" })
  expect(october).toMatchObject({ total_score: 7, severity: "mild" })

  await page.goto(`/dashboard/patients/${patient.id}`)
  await page.getByRole("tab", { name: /Measures/ }).click()
  const tab = page.getByRole("tabpanel")

  await expect(tab.getByRole("heading", { name: "PHQ-9" })).toBeVisible()
  await expect(tab.getByText("2 administrations")).toBeVisible()

  // The headline is the most recent administration, not the last one written.
  await expect(tab.getByTestId("outcome-latest-score")).toHaveText("7")
  await expect(tab.getByTestId("outcome-latest-severity")).toHaveText("mild")

  // The dated list reads newest first, each row its date, any safety
  // marker, the total and the server's label. Item 9 was endorsed in
  // September and not in October, so only September's row is marked.
  const rows = tab.getByRole("listitem")
  await expect(rows).toHaveCount(2)
  await expect(rows.nth(0)).toHaveText(`${shownDate(octoberAt)}7mild`)
  await expect(rows.nth(1)).toHaveText(`${shownDate(septemberAt)}Item 918moderately severe`)
  await expect(tab.getByTitle("Item 9 endorsed — assess safety")).toHaveCount(1)
  await expect(rows.nth(1).getByTitle("Item 9 endorsed — assess safety")).toHaveText("Item 9")

  // The trend line runs in date order: September's 18 on the left, drawn
  // above October's 7 on the right.
  const sparkline = tab.getByRole("img", { name: "Score trend" })
  await expect(sparkline).toBeVisible()
  const ys = await sparklineYs(sparkline)
  expect(ys).toHaveLength(2)
  expect(ys[0]).toBeLessThan(ys[1])
})
