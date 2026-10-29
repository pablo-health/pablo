// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Tasks tied to a client, end to end.
 *
 * - "Add a task" on a client's chart starts tied to that client; the task
 *   lists on their chart and, with their name, on the Tasks page.
 * - Marking it done takes it off the chart.
 * - A task tied to a client from the Tasks page, by searching their name,
 *   shows up on their chart.
 *
 * The access check (a client the clinician cannot open cannot be linked or
 * listed) is proven against the real grant table by the backend's route
 * test; this spec proves the screens agree with each other.
 */

import { test, expect } from "../fixtures/auth"
import { givePatient } from "../fixtures/scenarios"

type Task = { id: string; label: string; patient_id: string | null; patient_name: string | null }

test("a task added on a client's chart is theirs on the chart and on the Tasks page", async ({
  signedInPage: page,
  api,
}) => {
  const client = await givePatient(api, { first_name: "Omar" })
  const name = `${client.first_name} ${client.last_name}`
  const label = `Send the release form ${Date.now()}`
  let taskId: string | null = null
  try {
    await page.goto(`/dashboard/patients/${client.id}`)
    const tasks = page.getByTestId("client-tasks")
    await tasks.getByRole("button", { name: "Add a task" }).click()

    const dialog = page.getByRole("dialog")
    await expect(dialog.getByTestId("task-client")).toContainText(name)
    await dialog.getByLabel("Title").fill(label)
    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/tasks" && r.request().method() === "POST",
    )
    await dialog.getByRole("button", { name: "Save task" }).click()
    const response = await created
    expect(response.status(), await response.text()).toBe(201)
    const task = (await response.json()) as Task
    taskId = task.id
    expect(task.patient_id).toBe(client.id)

    await expect(tasks.getByTestId(`compliance-item-${task.id}`)).toContainText(label)

    // On the Tasks page, the row names the client. Undated, so under "All".
    await page.goto(`/dashboard/settings/tasks?item=${task.id}`)
    const row = page.getByTestId(`compliance-item-${task.id}`)
    await expect(row).toContainText(name)

    // Done: off the chart.
    await api.post(`/api/tasks/${task.id}/complete`, {})
    await page.goto(`/dashboard/patients/${client.id}`)
    await expect(page.getByTestId("client-tasks")).toBeVisible()
    await expect(page.getByText(label)).toHaveCount(0)
  } finally {
    if (taskId) await api.request("DELETE", `/api/tasks/${taskId}`).catch(() => undefined)
  }
})

test("a task tied to a client by name on the Tasks page shows on their chart", async ({
  signedInPage: page,
  api,
}) => {
  const client = await givePatient(api, { first_name: `Rosa${Date.now().toString(36)}` })
  const label = `Fax the referral ${Date.now()}`
  let taskId: string | null = null
  try {
    await page.goto("/dashboard/settings/tasks")
    await page.getByRole("button", { name: "Add a task" }).first().click()
    const dialog = page.getByRole("dialog")
    await dialog.getByRole("radio", { name: "Practice" }).click()
    await dialog.getByLabel("Title").fill(label)
    // "Client" or "Patient", by the clinician's word; the form's other combobox is Repeats.
    await dialog.getByRole("combobox", { name: /client|patient/i }).fill(client.first_name)
    await dialog.getByRole("option", { name: `${client.first_name} ${client.last_name}` }).click()

    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/tasks" && r.request().method() === "POST",
    )
    await dialog.getByRole("button", { name: "Save task" }).click()
    const response = await created
    expect(response.status(), await response.text()).toBe(201)
    const task = (await response.json()) as Task
    taskId = task.id
    expect(task.patient_id).toBe(client.id)

    await page.goto(`/dashboard/patients/${client.id}`)
    await expect(
      page.getByTestId("client-tasks").getByTestId(`compliance-item-${task.id}`),
    ).toContainText(label)
  } finally {
    if (taskId) await api.request("DELETE", `/api/tasks/${taskId}`).catch(() => undefined)
  }
})
