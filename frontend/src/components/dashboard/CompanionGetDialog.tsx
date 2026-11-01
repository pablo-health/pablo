// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import Image from "next/image"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { CopyButton } from "@/components/settings/domains/CopyButton"
import {
  companionNeedsServerAddress,
  companionPlatform,
} from "@/lib/companion"
import { useCompanionDownloadUrl } from "@/lib/companion.extensions"

interface CompanionGetDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CompanionGetDialog({
  open,
  onOpenChange,
}: CompanionGetDialogProps) {
  const platform = companionPlatform()
  const mac = platform === "macos"
  const windows = platform === "windows"
  const name = mac ? "macOS" : "Windows"
  const downloadUrl = useCompanionDownloadUrl(platform)
  // The desktop app signs in to its default server unless told otherwise, so
  // anywhere else it needs this deployment's address once. The browser's
  // origin is that address: the app signs in through the same web sign-in
  // this page is served from.
  const origin = typeof window === "undefined" ? "" : window.location.origin
  const showAddress = origin !== "" && companionNeedsServerAddress(origin)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-sm text-center">
        <div className="flex justify-center pt-2">
          <Image
            src="/pablo-tie.webp"
            alt="Pablo bear"
            width={72}
            height={72}
            priority
          />
        </div>
        <DialogHeader className="items-center">
          <DialogTitle className="font-display">
            {mac
              ? "Get Pablo for Mac"
              : windows
                ? "Get Pablo for Windows"
                : "Pablo desktop app"}
          </DialogTitle>
          <DialogDescription className="text-center">
            {mac || windows
              ? "Recording and transcription happen in the Pablo desktop app. Download it once and it works alongside every session."
              : "Recording and transcription happen in the Pablo desktop app, available for macOS and Windows."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 pt-1">
          {platform && downloadUrl ? (
            <>
              <Button asChild>
                <a href={downloadUrl} target="_blank" rel="noreferrer">
                  Download for {name}
                </a>
              </Button>
              {/* The dashboard only learns the app is installed once someone
                  signs in from it, so say so where they download it. */}
              {showAddress ? (
                <ServerAddress origin={origin} />
              ) : (
                <p className="text-xs text-neutral-500">
                  After installing, open the app and sign in.
                </p>
              )}
            </>
          ) : platform ? (
            <>
              <Button disabled>Download for {name}</Button>
              <p className="text-xs text-neutral-500">
                Download unavailable — install later from Settings.
              </p>
            </>
          ) : (
            <p className="text-xs text-neutral-500">
              You&apos;re on a platform that isn&apos;t supported yet.
              Check back soon.
            </p>
          )}
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function ServerAddress({ origin }: { origin: string }) {
  return (
    <div className="space-y-1.5">
      <p className="text-xs text-neutral-500">
        After installing, open the app, choose Connect to a different server,
        enter this address, and sign in.
      </p>
      <div className="flex items-center justify-center gap-1">
        <code className="rounded bg-muted px-2 py-1 font-mono text-xs break-all">
          {origin}
        </code>
        <CopyButton text={origin} label="Copy this Pablo's address" />
      </div>
    </div>
  )
}
