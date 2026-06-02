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
import { isMacOS } from "@/lib/companion"

interface CompanionGetDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CompanionGetDialog({
  open,
  onOpenChange,
}: CompanionGetDialogProps) {
  const mac = isMacOS()

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
            {mac ? "Get Pablo for Mac" : "Pablo desktop app"}
          </DialogTitle>
          <DialogDescription className="text-center">
            {mac
              ? "Recording and transcription happen in the Pablo desktop app. Download it once and it works alongside every session."
              : "Recording and transcription happen in the Pablo desktop app, available for macOS. Windows support is on the roadmap."}
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-2 pt-1">
          {mac ? (
            <Button asChild>
              <a
                href="https://pablo.health"
                target="_blank"
                rel="noreferrer"
              >
                Download for macOS
              </a>
            </Button>
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
