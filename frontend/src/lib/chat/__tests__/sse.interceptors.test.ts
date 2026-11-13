// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * The chat stream calls `fetch` itself, so a failed turn never reaches
 * `apiClient`. Registered API error interceptors must still see it: one that
 * marks the failure `handledExternally` has explained it elsewhere, and the
 * panel must not add its own reading of the status ("not signed in" for a
 * 403). A failure no interceptor claims must read exactly as before.
 *
 * Runs against the real `client.ts`; only the extension slot and the auth
 * header are replaced.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

import type { ApiErrorInterceptor } from "@/lib/api/client"

const { interceptors } = vi.hoisted(() => ({
  interceptors: [] as ApiErrorInterceptor[],
}))

vi.mock("@/lib/api/client.extensions", () => ({
  apiErrorInterceptors: interceptors,
}))

vi.mock("@/lib/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/api/client")>()),
  getAuthHeader: vi.fn().mockResolvedValue({ Authorization: "Bearer tok" }),
}))

import { streamChatMessages } from "../sse"

const CLAIMED_CODE = "claimed_elsewhere"

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  })
}

/** Claims a 403 whose body carries CLAIMED_CODE, reading a clone of the body. */
const claimingInterceptor: ApiErrorInterceptor = async (response) => {
  if (response.status !== 403) return null
  const body = (await response.clone().json()) as { detail?: { code?: string } }
  if (body.detail?.code !== CLAIMED_CODE) return null
  return { handledExternally: true, message: "Explained elsewhere" }
}

function callbacks() {
  return { onMeta: vi.fn(), onDelta: vi.fn(), onDone: vi.fn(), onError: vi.fn() }
}

beforeEach(() => {
  interceptors.length = 0
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("streamChatMessages with registered error interceptors", () => {
  it("reports a claimed 403 as handled_externally, never auth_denied", async () => {
    interceptors.push(claimingInterceptor)
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(jsonResponse(403, { detail: { code: CLAIMED_CODE } })),
    )
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(cb.onError).toHaveBeenCalledTimes(1)
    expect(cb.onError).toHaveBeenCalledWith({
      error: "handled_externally",
      message: "Explained elsewhere",
    })
  })

  it("passes an empty message when the interceptor supplies none", async () => {
    interceptors.push(async () => ({ handledExternally: true }))
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(403, {})))
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(cb.onError).toHaveBeenCalledWith({ error: "handled_externally", message: "" })
  })

  it("still reads a 403 no interceptor claimed as auth_denied, with its body", async () => {
    interceptors.push(claimingInterceptor)
    const body = { detail: "Not allowed" }
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(403, body)))
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(cb.onError).toHaveBeenCalledWith({
      error: "auth_denied",
      message: JSON.stringify(body),
    })
  })

  it("ignores interceptor data that does not claim the failure", async () => {
    interceptors.push(async () => ({ somethingElse: true, message: "ignored" }))
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(409, {})))
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(cb.onError).toHaveBeenCalledWith({ error: "concurrent_turn", message: "{}" })
  })

  it("falls back to its own error when an interceptor throws", async () => {
    interceptors.push(async () => {
      throw new Error("interceptor bug")
    })
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(403, {})))
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(cb.onError).toHaveBeenCalledWith(expect.objectContaining({ error: "auth_denied" }))
  })

  it("does not run interceptors on a successful stream", async () => {
    const spy = vi.fn().mockResolvedValue({ handledExternally: true })
    interceptors.push(spy)
    const stream = "event: done\ndata: {\"output_tokens\": 1}\n\n"
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } }),
      ),
    )
    const cb = callbacks()

    await streamChatMessages("conv-1", { content: "hi" }, cb)

    expect(spy).not.toHaveBeenCalled()
    expect(cb.onDone).toHaveBeenCalledTimes(1)
    expect(cb.onError).not.toHaveBeenCalled()
  })
})
