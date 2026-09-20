// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * MessageComposer, the attaching half: pick, upload, chip, send.
 *
 * The claim worth proving is the ordering. A send names documents that
 * already exist, so the upload has to finish before the send carries the
 * id — and a failed upload has to leave the words sendable on their own,
 * because that is the state somebody gives up in.
 */

import { describe, expect, it, vi } from "vitest"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MAX_ATTACHMENTS, MessageComposer } from "../MessageComposer"
import type { ComposerAttachment } from "../MessageComposer"

function aFile(name = "insurance-card.png"): File {
  return new File(["not really a png"], name, { type: "image/png" })
}

function uploaded(documentId: string, filename: string): ComposerAttachment {
  return { documentId, filename, sizeBytes: 2048 }
}

async function pick(user: ReturnType<typeof userEvent.setup>, file: File) {
  await user.upload(screen.getByTestId("portal-messaging-composer-file"), file)
}

describe("MessageComposer attachments", () => {
  it("offers no attach button when there is nowhere to put a file", () => {
    render(<MessageComposer onSend={vi.fn()} sending={false} />)

    expect(screen.queryByTestId("portal-messaging-composer-attach")).toBeNull()
  })

  it("uploads the picked file and shows it as a chip", async () => {
    const onAttach = vi.fn().mockResolvedValue(uploaded("doc-1", "insurance-card.png"))
    const user = userEvent.setup()
    render(
      <MessageComposer onSend={vi.fn()} sending={false} onAttach={onAttach} />,
    )

    await pick(user, aFile())

    const chip = await screen.findByTestId("portal-messaging-composer-chip-doc-1")
    expect(chip.textContent).toContain("insurance-card.png")
    expect(chip.textContent).toContain("2 KB")
    expect(onAttach).toHaveBeenCalledTimes(1)
  })

  it("sends the ids of what was attached, then clears them", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined)
    const onAttach = vi.fn().mockResolvedValue(uploaded("doc-1", "card.png"))
    const user = userEvent.setup()
    render(<MessageComposer onSend={onSend} sending={false} onAttach={onAttach} />)

    await pick(user, aFile())
    await screen.findByTestId("portal-messaging-composer-chip-doc-1")
    await user.type(
      screen.getByTestId("portal-messaging-composer-body"),
      "Here is the card",
    )
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    expect(onSend).toHaveBeenCalledWith("Here is the card", ["doc-1"])
    await waitFor(() =>
      expect(screen.queryByTestId("portal-messaging-composer-chip-doc-1")).toBeNull(),
    )
  })

  it("keeps the chips when the send fails", async () => {
    const onSend = vi.fn().mockRejectedValue(new Error("nope"))
    const onAttach = vi.fn().mockResolvedValue(uploaded("doc-1", "card.png"))
    const user = userEvent.setup()
    render(<MessageComposer onSend={onSend} sending={false} onAttach={onAttach} />)

    await pick(user, aFile())
    await screen.findByTestId("portal-messaging-composer-chip-doc-1")
    await user.type(screen.getByTestId("portal-messaging-composer-body"), "Take two")
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    await waitFor(() => expect(onSend).toHaveBeenCalled())
    expect(screen.getByTestId("portal-messaging-composer-chip-doc-1")).toBeTruthy()
  })

  it("a removed chip is not sent", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined)
    const onAttach = vi
      .fn()
      .mockResolvedValueOnce(uploaded("doc-1", "one.png"))
      .mockResolvedValueOnce(uploaded("doc-2", "two.png"))
    const user = userEvent.setup()
    render(<MessageComposer onSend={onSend} sending={false} onAttach={onAttach} />)

    await pick(user, aFile("one.png"))
    await screen.findByTestId("portal-messaging-composer-chip-doc-1")
    await pick(user, aFile("two.png"))
    await screen.findByTestId("portal-messaging-composer-chip-doc-2")

    await user.click(screen.getByTestId("portal-messaging-composer-remove-doc-1"))
    await user.type(screen.getByTestId("portal-messaging-composer-body"), "Just the one")
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    expect(onSend).toHaveBeenCalledWith("Just the one", ["doc-2"])
  })

  it("a failed upload offers a retry and does not block sending the words", async () => {
    const onSend = vi.fn().mockResolvedValue(undefined)
    const onAttach = vi.fn().mockRejectedValue(new Error("storage is down"))
    const user = userEvent.setup()
    render(<MessageComposer onSend={onSend} sending={false} onAttach={onAttach} />)

    await pick(user, aFile())

    expect(
      (await screen.findByTestId("portal-messaging-composer-attach-error")).textContent,
    ).toContain("That file didn't upload.")

    await user.type(
      screen.getByTestId("portal-messaging-composer-body"),
      "Sending without it",
    )
    await user.click(screen.getByTestId("portal-messaging-composer-send"))

    expect(onSend).toHaveBeenCalledWith("Sending without it", [])
  })

  it("retrying the same file attaches it when the upload works", async () => {
    const onAttach = vi
      .fn()
      .mockRejectedValueOnce(new Error("storage is down"))
      .mockResolvedValueOnce(uploaded("doc-1", "card.png"))
    const user = userEvent.setup()
    render(<MessageComposer onSend={vi.fn()} sending={false} onAttach={onAttach} />)

    await pick(user, aFile("card.png"))
    await screen.findByTestId("portal-messaging-composer-attach-error")

    await user.click(screen.getByTestId("portal-messaging-composer-attach-retry"))

    expect(
      await screen.findByTestId("portal-messaging-composer-chip-doc-1"),
    ).toBeTruthy()
    await waitFor(() =>
      expect(screen.queryByTestId("portal-messaging-composer-attach-error")).toBeNull(),
    )
    expect(onAttach).toHaveBeenCalledTimes(2)
  })

  it("stops offering to attach once the message is full", async () => {
    const onAttach = vi
      .fn()
      .mockImplementation(async (file: File) => uploaded(file.name, file.name))
    const user = userEvent.setup()
    render(<MessageComposer onSend={vi.fn()} sending={false} onAttach={onAttach} />)

    for (let n = 0; n < MAX_ATTACHMENTS; n++) {
      await pick(user, aFile(`file-${n}.png`))
      await screen.findByTestId(`portal-messaging-composer-chip-file-${n}.png`)
    }

    expect(
      (
        screen.getByTestId("portal-messaging-composer-attach") as HTMLButtonElement
      ).disabled,
    ).toBe(true)
  })
})
