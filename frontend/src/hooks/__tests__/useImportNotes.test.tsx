// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * useImportNotes hook tests
 *
 * Real QueryClient, mocked import API. Focus on the bulk-orchestration
 * contract: every file gets a terminal status, and one failing file does
 * not block the rest of the batch.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { act, renderHook, waitFor } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { useImportNotes } from "../useImportNotes"
import * as sessionsApi from "@/lib/api/sessions"
import type { SessionResponse } from "@/types/sessions"

vi.mock("@/lib/api/sessions")

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  Wrapper.displayName = "QueryWrapper"
  return Wrapper
}

function makeFile(name: string): File {
  return new File(["content"], name, { type: "application/pdf" })
}

function sessionFor(name: string): SessionResponse {
  return {
    id: `session-${name}`,
    session_date: "2026-02-04T00:00:00",
  } as SessionResponse
}

describe("useImportNotes", () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it("imports every file and marks each done", async () => {
    vi.mocked(sessionsApi.importNote).mockImplementation((_pid, file) =>
      Promise.resolve(sessionFor(file.name)),
    )

    const { result } = renderHook(() => useImportNotes("patient-1"), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.start([makeFile("a.pdf"), makeFile("b.pdf")])
    })

    await waitFor(() => expect(result.current.isComplete).toBe(true))
    expect(result.current.items).toHaveLength(2)
    expect(result.current.items.every((i) => i.status === "done")).toBe(true)
    expect(result.current.doneCount).toBe(2)
    expect(result.current.errorCount).toBe(0)
    expect(result.current.items[0].session?.id).toBe("session-a.pdf")
  })

  it("isolates a failing file without blocking the others", async () => {
    vi.mocked(sessionsApi.importNote).mockImplementation((_pid, file) =>
      file.name === "bad.pdf"
        ? Promise.reject(new Error("Couldn't read this PDF"))
        : Promise.resolve(sessionFor(file.name)),
    )

    const { result } = renderHook(() => useImportNotes("patient-1"), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.start([
        makeFile("ok1.pdf"),
        makeFile("bad.pdf"),
        makeFile("ok2.pdf"),
      ])
    })

    await waitFor(() => expect(result.current.isComplete).toBe(true))
    expect(result.current.doneCount).toBe(2)
    expect(result.current.errorCount).toBe(1)

    const failed = result.current.items.find((i) => i.file.name === "bad.pdf")
    expect(failed?.status).toBe("error")
    expect(failed?.error).toBe("Couldn't read this PDF")
  })

  it("calls the import endpoint once per file", async () => {
    vi.mocked(sessionsApi.importNote).mockResolvedValue(sessionFor("x"))

    const { result } = renderHook(() => useImportNotes("patient-42"), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      await result.current.start([makeFile("a.pdf"), makeFile("b.pdf"), makeFile("c.pdf")])
    })

    expect(sessionsApi.importNote).toHaveBeenCalledTimes(3)
    expect(sessionsApi.importNote).toHaveBeenCalledWith(
      "patient-42",
      expect.any(File),
      expect.objectContaining({ token: undefined }),
    )
  })
})
