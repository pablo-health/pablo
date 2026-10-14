// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { afterEach, describe, expect, it, vi } from "vitest"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { OriginalFileButton } from "../OriginalFileButton"

const { getUrl } = vi.hoisted(() => ({ getUrl: vi.fn() }))
vi.mock("@/lib/api/patientDocuments", () => ({ getPatientDocumentDownloadUrl: getUrl }))

afterEach(() => {
  vi.restoreAllMocks()
  getUrl.mockReset()
})

describe("OriginalFileButton", () => {
  it("opens the kept file in a new tab, to be read in place", async () => {
    const tab = { opener: {}, location: { href: "" }, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window)
    getUrl.mockResolvedValue("https://storage.example/doc-1?sig=abc")

    render(<OriginalFileButton documentId="doc-1" />)
    await userEvent.click(screen.getByRole("button", { name: "Open original file" }))

    expect(getUrl).toHaveBeenCalledWith("doc-1", undefined, "inline")
    expect(tab.location.href).toBe("https://storage.example/doc-1?sig=abc")
    expect(tab.opener).toBeNull()
  })

  it("says so when the file can't be opened", async () => {
    const tab = { opener: {}, location: { href: "" }, close: vi.fn() }
    vi.spyOn(window, "open").mockReturnValue(tab as unknown as Window)
    getUrl.mockRejectedValue(new Error("not found"))

    render(<OriginalFileButton documentId="doc-1" />)
    await userEvent.click(screen.getByRole("button", { name: "Open original file" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("The file didn't open.")
    expect(tab.close).toHaveBeenCalled()
  })
})
