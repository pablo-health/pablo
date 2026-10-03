// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Settings > Website — what the practice is told about where its site stands,
 * what the owner can do, and what everyone else cannot.
 *
 * The page must never call the site live unless the server names a live host
 * (a published version AND a working website domain), so each state is pinned
 * to its words here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"

import { WebsitePage } from "../WebsitePage"
import { renderWithProviders } from "@/test/renderWithProviders"
import { ApiError } from "@/lib/api/client"
import type { PracticeSite, SiteThemeReport } from "@/lib/api/practiceSite"

const mockGet = vi.fn()
const mockUpload = vi.fn()
const mockDiscard = vi.fn()
const mockPreview = vi.fn()
const mockPublish = vi.fn()
const mockRollBack = vi.fn()
const mockStatus = vi.fn()

vi.mock("@/lib/api/practiceSite", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/practiceSite")>()),
  getPracticeSite: (...a: unknown[]) => mockGet(...a),
  uploadPracticeSiteDraft: (...a: unknown[]) => mockUpload(...a),
  discardPracticeSiteDraft: (...a: unknown[]) => mockDiscard(...a),
  previewPracticeSiteDraft: (...a: unknown[]) => mockPreview(...a),
  publishPracticeSite: (...a: unknown[]) => mockPublish(...a),
  rollBackPracticeSite: (...a: unknown[]) => mockRollBack(...a),
}))

vi.mock("@/lib/api/users", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/users")>()),
  getUserStatus: (...a: unknown[]) => mockStatus(...a),
}))

vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  buildApiUrl: (endpoint: string) => `https://api.example.com${endpoint}`,
}))

const WHEN = "2026-09-01T15:04:00Z"

function site(overrides: Partial<PracticeSite> = {}): PracticeSite {
  return {
    enabled: true,
    live_version: null,
    live_host: null,
    has_active_host: false,
    draft: null,
    versions: [],
    ...overrides,
  }
}

const PUBLISHED = site({
  live_version: 2,
  live_host: "www.example.com",
  has_active_host: true,
  versions: [
    { version: 2, file_count: 3, total_bytes: 2048, published_at: WHEN, is_live: true, has_theme: true },
    { version: 1, file_count: 1, total_bytes: 100, published_at: WHEN, is_live: false, has_theme: false },
  ],
})

const WITH_DRAFT = site({ draft: { file_count: 3, total_bytes: 2048, uploaded_at: WHEN, theme: null } })

const NO_COLORS = { accent: null, accentText: null, background: null, surface: null, text: null, mutedText: null }

function withTheme(theme: SiteThemeReport): PracticeSite {
  return site({ draft: { file_count: 4, total_bytes: 2048, uploaded_at: WHEN, theme } })
}

describe("WebsitePage", () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStatus.mockResolvedValue({ is_practice_owner: true })
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("names the live address only when the server gives one", async () => {
    mockGet.mockResolvedValue(PUBLISHED)
    renderWithProviders(<WebsitePage />)

    const live = await screen.findByTestId("website-live")
    expect(live).toHaveTextContent("Live at www.example.com")
    expect(within(live).getByRole("link", { name: "www.example.com" })).toHaveAttribute(
      "href",
      "https://www.example.com",
    )
  })

  it("says a published site goes live once a website domain works, and links to Domains", async () => {
    mockGet.mockResolvedValue(site({ live_version: 1 }))
    renderWithProviders(<WebsitePage />)

    const live = await screen.findByTestId("website-live")
    expect(live).toHaveTextContent("It goes live when a website domain is active in Domains.")
    expect(live).not.toHaveTextContent("Live at")
    expect(within(live).getByRole("link", { name: "Domains" })).toHaveAttribute(
      "href",
      "/dashboard/settings/domains",
    )
  })

  it("says nothing is published, even with an active domain", async () => {
    mockGet.mockResolvedValue(site({ has_active_host: true }))
    renderWithProviders(<WebsitePage />)

    expect(await screen.findByTestId("website-live")).toHaveTextContent("Not published yet.")
    expect(screen.getByTestId("website-draft")).toHaveTextContent("No draft yet.")
    expect(screen.queryByRole("heading", { name: "Versions" })).not.toBeInTheDocument()
  })

  it("offers nothing on a deployment that does not publish websites", async () => {
    mockGet.mockResolvedValue(site({ enabled: false }))
    renderWithProviders(<WebsitePage />)

    expect(await screen.findByText("Publishing a website isn't turned on for this deployment.")).toBeVisible()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("shows someone other than the owner the site, and no controls", async () => {
    mockStatus.mockResolvedValue({ is_practice_owner: false })
    mockGet.mockResolvedValue({ ...PUBLISHED, draft: WITH_DRAFT.draft })
    renderWithProviders(<WebsitePage />)

    expect(await screen.findByText("Only the practice owner can change the website.")).toBeVisible()
    expect(screen.getByTestId("website-version-1")).toBeVisible()
    expect(screen.queryByRole("button")).not.toBeInTheDocument()
  })

  it("uploads the chosen zip as the draft", async () => {
    mockGet.mockResolvedValue(site())
    mockUpload.mockResolvedValue(WITH_DRAFT)
    renderWithProviders(<WebsitePage />)

    const zip = new File(["PK"], "site.zip", { type: "application/zip" })
    await userEvent.upload(await screen.findByLabelText("Website zip"), zip)

    expect(mockUpload).toHaveBeenCalledWith(zip)
    expect(await screen.findByTestId("website-draft")).toHaveTextContent("3 files, 2 KB, uploaded")
  })

  it("shows the server's reason a zip was refused", async () => {
    mockGet.mockResolvedValue(site())
    mockUpload.mockRejectedValue(
      new ApiError("INVALID_SITE", "The folder needs an index.html at the top.", undefined, 422),
    )
    renderWithProviders(<WebsitePage />)

    await userEvent.upload(
      await screen.findByLabelText("Website zip"),
      new File(["PK"], "site.zip", { type: "application/zip" }),
    )

    expect(await screen.findByRole("alert")).toHaveTextContent("The folder needs an index.html at the top.")
  })

  it("publishes the draft", async () => {
    mockGet.mockResolvedValue(WITH_DRAFT)
    mockPublish.mockResolvedValue(PUBLISHED)
    renderWithProviders(<WebsitePage />)

    await userEvent.click(await screen.findByRole("button", { name: "Publish" }))

    expect(mockPublish).toHaveBeenCalled()
    expect(await screen.findByTestId("website-live")).toHaveTextContent("Live at www.example.com")
  })

  it("rolls back to an earlier version, and offers no roll back on the current one", async () => {
    mockGet.mockResolvedValue(PUBLISHED)
    mockRollBack.mockResolvedValue(PUBLISHED)
    renderWithProviders(<WebsitePage />)

    const current = await screen.findByTestId("website-version-2")
    expect(within(current).getByText("Current")).toBeVisible()
    expect(within(current).queryByRole("button")).not.toBeInTheDocument()

    await userEvent.click(within(screen.getByTestId("website-version-1")).getByRole("button", { name: "Roll back" }))
    expect(mockRollBack).toHaveBeenCalledWith(1)
  })

  it("opens the draft's preview on the API's origin in a new tab", async () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null)
    mockGet.mockResolvedValue(WITH_DRAFT)
    mockPreview.mockResolvedValue({ path: "/api/practice/website/preview/tok/", expires_at: WHEN })
    renderWithProviders(<WebsitePage />)

    // The preview is inert; the page says so beside the button.
    expect(
      await screen.findByText("The preview shows your pages. Scripts run once the site is live on your domain."),
    ).toBeInTheDocument()
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }))

    await vi.waitFor(() =>
      expect(open).toHaveBeenCalledWith(
        "https://api.example.com/api/practice/website/preview/tok/",
        "_blank",
        "noopener",
      ),
    )
  })

  it("says what the draft's theme.json gives the portal, and what it leaves out and why", async () => {
    mockGet.mockResolvedValue(
      withTheme({
        theme: {
          version: 1,
          colors: { ...NO_COLORS, accent: "#24504c", accentText: "#ffffff" },
          fonts: { heading: "Fraunces", body: null },
          radius: null,
          header: null,
        },
        skipped: [{ field: "colors.text", reason: "Too little contrast with background to read easily." }],
      }),
    )
    renderWithProviders(<WebsitePage />)

    const theme = await screen.findByTestId("website-theme")
    expect(theme).toHaveTextContent(
      "Once this is published, your portal on your own domain will use the colors and fonts from theme.json.",
    )
    expect(theme).toHaveTextContent("Not used from theme.json:")
    expect(within(theme).getByRole("listitem")).toHaveTextContent(
      "colors.text: Too little contrast with background to read easily.",
    )
  })

  it("says the portal takes the header, and lists each header value it leaves out and why", async () => {
    mockGet.mockResolvedValue(
      withTheme({
        theme: {
          version: 1,
          colors: NO_COLORS,
          fonts: { heading: null, body: null },
          radius: null,
          header: {
            wordmark: "Riverside Counseling",
            subtitle: null,
            links: [{ label: "About", href: "/about" }],
            cta: null,
          },
        },
        skipped: [
          { field: "header.links[1].href", reason: "Must be a page on your website, like /about." },
          { field: "header.cta.label", reason: "Mixes alphabets in a way browsers warn about." },
        ],
      }),
    )
    renderWithProviders(<WebsitePage />)

    const theme = await screen.findByTestId("website-theme")
    expect(theme).toHaveTextContent(
      "Once this is published, your portal on your own domain will use the header from theme.json.",
    )
    expect(within(theme).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "header.links[1].href: Must be a page on your website, like /about.",
      "header.cta.label: Mixes alphabets in a way browsers warn about.",
    ])
  })

  it("says only why when nothing in theme.json could be used", async () => {
    mockGet.mockResolvedValue(
      withTheme({ theme: null, skipped: [{ field: "theme.json", reason: "Isn't valid JSON." }] }),
    )
    renderWithProviders(<WebsitePage />)

    const theme = await screen.findByTestId("website-theme")
    expect(theme).not.toHaveTextContent("will use")
    expect(within(theme).getByRole("listitem")).toHaveTextContent("theme.json: Isn't valid JSON.")
  })

  it("says nothing about a theme when the draft has no theme.json", async () => {
    mockGet.mockResolvedValue(WITH_DRAFT)
    renderWithProviders(<WebsitePage />)

    expect(await screen.findByTestId("website-draft")).toBeVisible()
    expect(screen.queryByTestId("website-theme")).not.toBeInTheDocument()
  })

  it("marks the versions that give the portal a theme", async () => {
    mockGet.mockResolvedValue(PUBLISHED)
    renderWithProviders(<WebsitePage />)

    expect(await screen.findByTestId("website-version-2")).toHaveTextContent("Portal theme")
    expect(screen.getByTestId("website-version-1")).not.toHaveTextContent("Portal theme")
  })

  it("discards the draft", async () => {
    mockGet.mockResolvedValue(WITH_DRAFT)
    mockDiscard.mockResolvedValue(site())
    renderWithProviders(<WebsitePage />)

    await userEvent.click(await screen.findByRole("button", { name: "Discard" }))

    expect(mockDiscard).toHaveBeenCalled()
    expect(await screen.findByText("No draft yet.")).toBeVisible()
  })
})
