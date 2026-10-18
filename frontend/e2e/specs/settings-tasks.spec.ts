// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Practice > Tasks, end to end.
 *
 * Compliance items became tasks. Three things only a running stack can show:
 *
 * - a compliance item filed the old way — through `/api/compliance`, as a
 *   client built before tasks would — is a compliance task on the new page,
 *   and the old Compliance address still finds it;
 * - a task from another category, added on the page, is due on the
 *   dashboard beside the compliance reminders;
 * - a compliance reminder added from the catalog shows on the dashboard and
 *   leaves it when deleted.
 *
 * That existing rows survive the table's migration is proven against
 * Postgres by the backend's migration test; this stack boots already at head.
 */

import { test, expect } from "../fixtures/auth"
import { BROWSER_TIME_ZONE } from "../fixtures/clock"

type ComplianceItem = { id: string; label: string; due_date: string | null }
type Task = ComplianceItem & { category: string; source: string; repeat_rule: string | null }

/**
 * `days` after today as the browser counts it, as the date input and the API
 * write it. "Today" is the browser's, not the runner's: the panel counts days
 * from the browser's midnight, and the two calendars disagree for part of
 * every day (the runner is UTC).
 */
function daysFromNow(days: number): string {
  const today = new Date().toLocaleDateString("en-CA", { timeZone: BROWSER_TIME_ZONE })
  const [y, m, d] = today.split("-").map(Number)
  return new Date(Date.UTC(y, m - 1, d + days)).toISOString().slice(0, 10)
}

test("a compliance item filed through the old API is a compliance task on Settings > Tasks", async ({
  signedInPage: page,
  api,
}) => {
  const label = `NY LMHC ${Date.now()}`
  const item = await api.post<ComplianceItem>("/api/compliance", {
    item_type: "license",
    label,
    due_date: daysFromNow(200),
  })
  try {
    const asTask = await api.get<Task>(`/api/tasks/${item.id}`)
    expect(asTask.category).toBe("compliance")
    expect(asTask.source).toBe("manual")

    // The old address lands on Tasks, filtered to compliance, on the item.
    await page.goto(`/dashboard/settings/compliance?item=${item.id}`)
    await expect(page).toHaveURL(/\/dashboard\/settings\/tasks\?/)
    await expect(page.getByRole("heading", { level: 1, name: "Tasks" })).toBeVisible()
    await expect(page.getByRole("radio", { name: /^Compliance/ })).toHaveAttribute(
      "aria-checked",
      "true",
    )
    const row = page.getByTestId(`compliance-item-${item.id}`)
    await expect(row).toHaveAttribute("aria-current", "true")
    await expect(row.getByTestId("compliance-item-details")).toContainText("License")
  } finally {
    await api.request("DELETE", `/api/compliance/${item.id}`).catch(() => undefined)
  }
})

test("a practice task added on Settings > Tasks is due on the dashboard", async ({
  signedInPage: page,
  api,
}) => {
  const label = `Renew the office lease ${Date.now()}`
  let taskId: string | null = null
  try {
    await page.goto("/dashboard/settings/tasks")
    await expect(page.getByRole("heading", { level: 1, name: "Tasks" })).toBeVisible()

    // A practice with no tasks yet sees the first-run button; one with tasks,
    // the header button. Both say the same thing.
    await page.getByRole("button", { name: "Add a task" }).first().click()
    const dialog = page.getByRole("dialog")
    await dialog.getByRole("radio", { name: "Practice" }).click()
    await dialog.getByLabel("Title").fill(label)
    await dialog.getByLabel("Due date (optional)").fill(daysFromNow(5))
    await dialog.getByLabel("Repeats").selectOption("yearly")

    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/tasks" && r.request().method() === "POST",
    )
    await dialog.getByRole("button", { name: "Save task" }).click()
    const response = await created
    expect(response.status(), await response.text()).toBe(201)
    const task = (await response.json()) as Task
    taskId = task.id
    expect(task.category).toBe("practice")
    expect(task.repeat_rule).toBe("yearly")

    // Not a compliance item: the old API, which only lists those, leaves it out.
    const compliance = await api.get<ComplianceItem[]>("/api/compliance")
    expect(compliance.map((i) => i.id)).not.toContain(task.id)

    // Due in five days, so on the dashboard's default "due now" view.
    await page.goto("/dashboard")
    await expect(page.getByRole("heading", { name: "Tasks", exact: true })).toBeVisible()
    const row = page.getByTestId(`compliance-item-${task.id}`)
    await expect(row).toContainText(label)
    await expect(row).toContainText("due in 5 days")
    await expect(row).toHaveAttribute("data-category", "practice")
  } finally {
    if (taskId) await api.request("DELETE", `/api/tasks/${taskId}`).catch(() => undefined)
  }
})

test("a compliance reminder added from the list shows on the dashboard and goes when deleted", async ({
  signedInPage: page,
  api,
}) => {
  const label = `Board renewal ${Date.now()}`
  let itemId: string | null = null
  try {
    await page.goto("/dashboard/settings/tasks?category=compliance")
    await page.getByRole("button", { name: "Add a task" }).first().click()
    const dialog = page.getByRole("dialog")
    // With nothing filed yet the task form opens first and offers the list.
    const fromList = dialog.getByRole("button", { name: "Choose it from the list" })
    if (await fromList.isVisible()) await fromList.click()
    await page.getByRole("button", { name: /Custom reminder/ }).click()
    await page.getByLabel("Label").fill(label)
    await page.locator("#composer-due").fill(daysFromNow(10))

    const created = page.waitForResponse(
      (r) => new URL(r.url()).pathname === "/api/tasks" && r.request().method() === "POST",
    )
    await page.getByRole("button", { name: "Save reminder" }).click()
    const createdResponse = await created
    expect(createdResponse.status(), await createdResponse.text()).toBe(201)
    const task = (await createdResponse.json()) as Task
    itemId = task.id
    expect(task.category).toBe("compliance")

    await page.goto("/dashboard")
    await expect(page.getByTestId(`compliance-item-${itemId}`)).toContainText(label)

    await api.request("DELETE", `/api/tasks/${itemId}`)
    itemId = null
    await page.goto("/dashboard")
    await expect(page.getByRole("heading", { name: "Tasks", exact: true })).toBeVisible()
    await expect(page.getByText(label)).toHaveCount(0)
  } finally {
    if (itemId) await api.request("DELETE", `/api/tasks/${itemId}`).catch(() => undefined)
  }
})
