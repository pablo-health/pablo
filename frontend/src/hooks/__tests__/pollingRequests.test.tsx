// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Every query that polls sends its requests as background requests.
 *
 * A visible tab polls whether or not anyone is at the keyboard, and a poll
 * the API counted as activity would keep an unattended screen signed in past
 * the idle sign-out. Each case renders a real poller against the real API
 * functions and apiClient, with only `fetch` stubbed, and checks the header
 * on what actually went out. That covers the whole chain: a poller that
 * forgot pollingOptions, or an API function that awaits something before
 * starting its request (which loses the mark), fails here.
 *
 * The session and note pages poll through `useSession` / `useNote` with an
 * interval; those hooks are covered here rather than the full pages.
 */
import type { ReactNode } from "react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { render, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { setApiUrl } from "@/lib/api/client"
import { resetWatchedDrafts, watchDraft } from "@/lib/draftWatch"
import { useDraftNotices } from "../useDraftNotices"
import { useInboxCount, useInboxItems } from "../useInbox"
import { useThread } from "../useMessageInbox"
import { useAppointmentList } from "../useAppointments"
import { useHeldGoogleRemovals } from "../useGoogleCalendarChanges"
import { useAutoBooked, useOutsideQuestions, useOutsideSessions } from "../useOutsideSessions"
import { useFloatingBlocks, useFloatingBlocksForDates } from "../useFloatingBlocks"
import { useSession, useSessionList, useSessionProcessing } from "../useSessions"
import { useNote } from "../useNotes"
import { useSessionDictations } from "../useDictations"
import { usePatientDocument, usePatientDocuments } from "../usePatientDocuments"
import { usePracticeDomains } from "../usePracticeDomains"
import { useImportRun } from "@/components/settings/import/useImport"
import { SessionStatusBadge } from "@/components/sessions/SessionStatusBadge"
import { SessionGeneratingOverlay } from "@/components/sessions/SessionGeneratingOverlay"

vi.mock("@/lib/auth/provider", () => ({
  getClientAuthProvider: () => ({ getIdToken: () => Promise.resolve("tok") }),
}))
vi.mock("@/lib/api/client.extensions", () => ({ apiErrorInterceptors: [] }))
vi.mock("@/lib/config", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/config")>()),
  useConfig: () => ({ dataMode: "api" }),
}))
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
}))

const SESSION = {
  id: "s1",
  patient_id: "p1",
  status: "processing",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
}

/** One body loose enough for every endpoint here; each hook reads its own part. */
function bodyFor(url: string): unknown {
  if (/\/api\/sessions(\?|$)/.test(url)) return { data: [SESSION], total: 1 }
  if (/\/api\/sessions\/s1$/.test(url)) return SESSION
  return {
    data: [],
    total: 0,
    items: [],
    blocks: [],
    events: [],
    sessions: [],
    domains: [],
    count: 0,
    status: "processing",
    state: "reading",
  }
}

const fetchMock = vi.fn((input: RequestInfo | URL, _init?: RequestInit) =>
  Promise.resolve(
    new Response(JSON.stringify(bodyFor(String(input))), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  ),
)

function sent(): { url: string; background: string | undefined }[] {
  return fetchMock.mock.calls.map(([url, init]) => ({
    url: String(url),
    background: (init?.headers as Record<string, string>)["X-Pablo-Background"],
  }))
}

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>
}

const START = "2026-01-04T00:00:00Z"
const END = "2026-01-11T00:00:00Z"
const DAY = "2026-01-05"

beforeEach(() => {
  setApiUrl("http://test")
  fetchMock.mockClear()
  vi.stubGlobal("fetch", fetchMock)
})

afterEach(() => {
  vi.unstubAllGlobals()
  resetWatchedDrafts()
})

/** A poller, and a path every one of its requests must reach before we check them. */
const POLLERS: [string, () => void, RegExp][] = [
  ["useDraftNotices (session list)", () => renderHook(useDraftNotices, { wrapper }), /\/api\/sessions(\?|$)/],
  [
    "useDraftNotices (each draft)",
    () => {
      watchDraft("s1")
      renderHook(useDraftNotices, { wrapper })
    },
    /\/api\/sessions\/s1$/,
  ],
  ["useInboxItems", () => renderHook(() => useInboxItems("open", []), { wrapper }), /inbox/],
  ["useInboxCount (nav badge)", () => renderHook(useInboxCount, { wrapper }), /inbox/],
  ["useThread", () => renderHook(() => useThread("t1"), { wrapper }), /t1/],
  ["useAppointmentList", () => renderHook(() => useAppointmentList(START, END), { wrapper }), /appointments/],
  ["useHeldGoogleRemovals", () => renderHook(useHeldGoogleRemovals, { wrapper }), /./],
  ["useOutsideSessions", () => renderHook(() => useOutsideSessions(START, END), { wrapper }), /./],
  ["useOutsideQuestions", () => renderHook(useOutsideQuestions, { wrapper }), /./],
  ["useAutoBooked", () => renderHook(useAutoBooked, { wrapper }), /./],
  ["useFloatingBlocks", () => renderHook(() => useFloatingBlocks(DAY), { wrapper }), new RegExp(DAY)],
  [
    "useFloatingBlocksForDates",
    () => renderHook(() => useFloatingBlocksForDates([DAY, "2026-01-06"]), { wrapper }),
    /2026-01-06/,
  ],
  ["useSessionProcessing", () => renderHook(() => useSessionProcessing("p1"), { wrapper }), /\/api\/sessions/],
  [
    "useSession with an interval (session page)",
    () => renderHook(() => useSession("s1", undefined, undefined, () => 3000), { wrapper }),
    /\/api\/sessions\/s1$/,
  ],
  [
    "SessionStatusBadge",
    () => render(<SessionStatusBadge status="processing" sessionId="s1" />, { wrapper }),
    /\/api\/sessions\/s1$/,
  ],
  [
    "SessionGeneratingOverlay (list, then the session)",
    () => render(<SessionGeneratingOverlay patientId="p1" />, { wrapper }),
    /\/api\/sessions\/s1$/,
  ],
  [
    "useNote with an interval (note page)",
    () => renderHook(() => useNote("n1", undefined, () => 3000), { wrapper }),
    /n1/,
  ],
  ["useSessionDictations", () => renderHook(() => useSessionDictations("s1"), { wrapper }), /dictations/],
  ["usePatientDocument", () => renderHook(() => usePatientDocument("d1"), { wrapper }), /d1/],
  ["useImportRun", () => renderHook(() => useImportRun("r1"), { wrapper }), /r1/],
  [
    "usePracticeDomains while polling",
    () => renderHook(() => usePracticeDomains(Date.now() + 60_000), { wrapper }),
    /./,
  ],
]

describe("polling queries send background requests", () => {
  it.each(POLLERS)("%s", async (_name, mount, path) => {
    mount()

    await waitFor(() => expect(sent().some(({ url }) => path.test(url))).toBe(true))
    expect(sent()).not.toHaveLength(0)
    for (const request of sent()) {
      expect(request, request.url).toMatchObject({ background: "1" })
    }
  })
})

describe("queries that do not poll stay foreground", () => {
  it.each<[string, () => void]>([
    ["useSessionList", () => renderHook(() => useSessionList(), { wrapper })],
    ["useSession", () => renderHook(() => useSession("s1"), { wrapper })],
    ["useNote", () => renderHook(() => useNote("n1"), { wrapper })],
    ["usePatientDocuments", () => renderHook(() => usePatientDocuments("p1"), { wrapper })],
  ])("%s", async (_name, mount) => {
    mount()

    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    for (const request of sent()) {
      expect(request.background, request.url).toBeUndefined()
    }
  })
})
