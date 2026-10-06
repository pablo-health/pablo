// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Dictate more, against a mocked microphone: record, stop, send; keep or
 * replace edits before a redraft; a signed note gets an addendum draft; and a
 * denied or missing microphone says so instead of failing silently.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { DictateMore } from "../DictateMore"
import type { SessionDictation } from "@/types/dictations"

const { listed } = vi.hoisted(() => ({ listed: { data: [] as SessionDictation[] } }))
vi.mock("@/hooks/useDictations", () => ({
  useSessionDictations: () => ({ data: listed }),
}))

class FakeMediaRecorder {
  static isTypeSupported = (type: string) => type === "audio/webm;codecs=opus"
  static instances: FakeMediaRecorder[] = []
  state: "inactive" | "recording" = "inactive"
  mimeType: string
  ondataavailable: ((event: { data: Blob }) => void) | null = null
  onstop: (() => void) | null = null

  constructor(_stream: MediaStream, options?: { mimeType?: string }) {
    this.mimeType = options?.mimeType ?? ""
    FakeMediaRecorder.instances.push(this)
  }

  start() {
    this.state = "recording"
  }

  stop() {
    this.state = "inactive"
    this.ondataavailable?.({ data: new Blob(["voice"], { type: this.mimeType }) })
    this.onstop?.()
  }
}

const track = { stop: vi.fn() }
const getUserMedia = vi.fn()

beforeEach(() => {
  FakeMediaRecorder.instances = []
  listed.data = []
  vi.stubGlobal("MediaRecorder", FakeMediaRecorder)
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: { getUserMedia },
  })
  getUserMedia.mockResolvedValue({ getTracks: () => [track] })
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.clearAllMocks()
})

function renderDictate(props: Partial<Parameters<typeof DictateMore>[0]> = {}) {
  const onSend = vi.fn().mockResolvedValue({ id: "d-1" })
  render(<DictateMore sessionId="s-1" signed={false} hasEdits={false} onSend={onSend} {...props} />)
  return onSend
}

async function recordAndStop() {
  await userEvent.click(screen.getByRole("button", { name: "Dictate more" }))
  expect(await screen.findByText(/Recording 0:00/)).toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "Stop" }))
}

describe("DictateMore", () => {
  it("records a clip and adds it to the note", async () => {
    const onSend = renderDictate()
    await recordAndStop()

    await userEvent.click(screen.getByRole("button", { name: "Add to note" }))

    expect(onSend).toHaveBeenCalledTimes(1)
    const [clip, edits] = onSend.mock.calls[0]
    expect(clip.blob.type).toBe("audio/webm;codecs=opus")
    expect(clip.blob.size).toBeGreaterThan(0)
    expect(edits).toBeUndefined()
    expect(track.stop).toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Dictate more" })).toBeInTheDocument()
  })

  it("asks what happens to edits first, keeping them by default", async () => {
    const onSend = renderDictate({ hasEdits: true })
    await recordAndStop()
    await userEvent.click(screen.getByRole("button", { name: "Add to note" }))

    const dialog = screen.getByRole("dialog")
    expect(screen.getByRole("radio", { name: /Keep my edits/ })).toBeChecked()
    expect(dialog).toHaveTextContent("The dictation goes into the rest.")
    await userEvent.click(screen.getByRole("radio", { name: /Redraft everything/ }))
    await userEvent.click(screen.getByRole("button", { name: "Add to note" }))

    expect(onSend.mock.calls[0][1]).toBe("replace")
  })

  it("drafts an addendum for a signed note without asking about edits", async () => {
    const onSend = renderDictate({ signed: true, hasEdits: true })
    await recordAndStop()
    await userEvent.click(screen.getByRole("button", { name: "Draft an addendum" }))

    expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
    expect(onSend).toHaveBeenCalledTimes(1)
  })

  it("throws a clip away without sending it", async () => {
    const onSend = renderDictate()
    await recordAndStop()
    await userEvent.click(screen.getByRole("button", { name: "Discard" }))

    expect(onSend).not.toHaveBeenCalled()
    expect(screen.getByRole("button", { name: "Dictate more" })).toBeInTheDocument()
  })

  it("says so when the microphone is refused", async () => {
    getUserMedia.mockRejectedValue(new DOMException("denied", "NotAllowedError"))
    renderDictate()
    await userEvent.click(screen.getByRole("button", { name: "Dictate more" }))

    expect(await screen.findByRole("alert")).toHaveTextContent("can't use your microphone")
    expect(FakeMediaRecorder.instances).toHaveLength(0)
  })

  it("says so when the browser can't record", async () => {
    vi.stubGlobal("MediaRecorder", undefined)
    renderDictate()
    await userEvent.click(screen.getByRole("button", { name: "Dictate more" }))

    expect(screen.getByRole("alert")).toHaveTextContent("This browser can't record audio")
  })

  it("follows the clip it sent while it is transcribed", async () => {
    const { rerender } = render(
      <DictateMore
        sessionId="s-1"
        signed={false}
        hasEdits={false}
        onSend={vi.fn().mockResolvedValue({ id: "d-1" })}
      />,
    )
    await recordAndStop()
    await userEvent.click(screen.getByRole("button", { name: "Add to note" }))

    listed.data = [{ id: "d-1", status: "transcribing" } as SessionDictation]
    rerender(
      <DictateMore sessionId="s-1" signed={false} hasEdits={false} onSend={vi.fn()} />,
    )
    expect(screen.getByText("Transcribing your dictation…")).toBeInTheDocument()

    listed.data = [{ id: "d-1", status: "failed" } as SessionDictation]
    await act(async () => {
      rerender(<DictateMore sessionId="s-1" signed={false} hasEdits={false} onSend={vi.fn()} />)
    })
    expect(screen.getByRole("alert")).toHaveTextContent("couldn't be transcribed")
  })
})
