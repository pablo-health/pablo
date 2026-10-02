// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useRef } from "react"
import { Button } from "@/components/ui/button"
import type { SiteDraft } from "@/lib/api/practiceSite"
import { SettingsCard } from "../ui"
import { WebsiteCreateOptions } from "../settingsSlots.extensions"
import { describeFiles, formatWhen } from "./format"

interface DraftCardProps {
  draft: SiteDraft | null
  canManage: boolean
  busy: boolean
  uploading: boolean
  onUpload: (file: File) => void
  onPreview: () => void
  onPublish: () => void
  onDiscard: () => void
  onDraftSaved: () => void
}

/** The draft: upload a zip, then preview it, publish it or discard it. */
export function DraftCard({
  draft,
  canManage,
  busy,
  uploading,
  onUpload,
  onPreview,
  onPublish,
  onDiscard,
  onDraftSaved,
}: DraftCardProps) {
  const input = useRef<HTMLInputElement>(null)

  return (
    <SettingsCard
      title="Draft"
      description="A zip of your site's folder, with index.html at the top. Preview it, then publish."
    >
      {draft ? (
        <p className="text-sm" data-testid="website-draft">
          {describeFiles(draft.file_count, draft.total_bytes)}, uploaded {formatWhen(draft.uploaded_at)}.
        </p>
      ) : (
        <p className="text-sm text-muted-foreground" data-testid="website-draft">
          No draft yet.
        </p>
      )}
      {canManage && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input
            ref={input}
            type="file"
            accept=".zip,application/zip"
            aria-label="Website zip"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              event.target.value = ""
              if (file) onUpload(file)
            }}
          />
          <Button size="sm" variant="outline" disabled={busy} onClick={() => input.current?.click()}>
            {uploading ? "Uploading…" : draft ? "Upload a new zip" : "Upload a zip"}
          </Button>
          <WebsiteCreateOptions canManage={canManage} onDraftSaved={onDraftSaved} />
          {draft && (
            <>
              <Button size="sm" variant="outline" disabled={busy} onClick={onPreview}>
                Preview
              </Button>
              <Button size="sm" disabled={busy} onClick={onPublish}>
                Publish
              </Button>
              <Button size="sm" variant="ghost" disabled={busy} onClick={onDiscard}>
                Discard
              </Button>
            </>
          )}
        </div>
      )}
    </SettingsCard>
  )
}
