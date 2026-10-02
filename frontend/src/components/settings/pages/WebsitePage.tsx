// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { useQueryClient } from "@tanstack/react-query"
import {
  practiceSiteKeys,
  useDiscardPracticeSiteDraft,
  usePracticeSite,
  usePreviewPracticeSiteDraft,
  usePublishPracticeSite,
  useRollBackPracticeSite,
  useUploadPracticeSiteDraft,
} from "@/hooks/usePracticeSite"
import { ApiError, buildApiUrl } from "@/lib/api/client"
import { canManageDomains } from "../domains/canManageDomains"
import { SettingsCard } from "../ui"
import { useSettingsUserStatus } from "../useSettingsPreferences"
import { DraftCard } from "../website/DraftCard"
import { LiveStatus } from "../website/LiveStatus"
import { VersionHistory } from "../website/VersionHistory"

/**
 * Practice > Website. The practice's static website: upload a zip as a draft,
 * preview it, publish it, and put an earlier version back. It is served at the
 * practice's website domains (Practice > Domains) once one of them works.
 */
export function WebsitePage() {
  const queryClient = useQueryClient()
  const { data: userStatus } = useSettingsUserStatus()
  const { data: site, isLoading, isError } = usePracticeSite()
  const upload = useUploadPracticeSiteDraft()
  const discard = useDiscardPracticeSiteDraft()
  const preview = usePreviewPracticeSiteDraft()
  const publish = usePublishPracticeSite()
  const rollBack = useRollBackPracticeSite()

  const canManage = canManageDomains(userStatus)
  const mutations = [upload, discard, preview, publish, rollBack]
  const busy = mutations.some((m) => m.isPending)
  const failure = mutations.map((m) => m.error).find(Boolean)
  const failureMessage =
    failure instanceof ApiError && failure.message
      ? failure.message
      : failure === upload.error && failure
        ? "That zip couldn't be uploaded. Try again."
        : failure
          ? "That change couldn't be saved. Try again."
          : null

  if (isLoading) return null
  if (isError || !site) {
    return (
      <SettingsCard>
        <p role="alert" className="text-sm text-muted-foreground">
          Your website couldn&apos;t be loaded. Try again.
        </p>
      </SettingsCard>
    )
  }
  if (!site.enabled) {
    return (
      <SettingsCard>
        <p className="text-sm text-muted-foreground">Publishing a website isn&apos;t turned on for this deployment.</p>
      </SettingsCard>
    )
  }

  const openPreview = () =>
    preview.mutate(undefined, {
      onSuccess: ({ path }) => {
        window.open(buildApiUrl(path), "_blank", "noopener")
      },
    })

  return (
    <>
      {userStatus && !canManage && (
        <p className="mb-3 text-[12.5px] text-muted-foreground">Only the practice owner can change the website.</p>
      )}
      {failureMessage && (
        <p role="alert" className="mb-3 text-[12.5px] text-red-700">
          {failureMessage}
        </p>
      )}
      <SettingsCard title="Your website">
        <LiveStatus site={site} />
      </SettingsCard>
      <DraftCard
        draft={site.draft}
        canManage={canManage}
        busy={busy}
        uploading={upload.isPending}
        onUpload={(file) => upload.mutate(file)}
        onPreview={openPreview}
        onPublish={() => publish.mutate()}
        onDiscard={() => discard.mutate()}
        onDraftSaved={() => void queryClient.invalidateQueries({ queryKey: practiceSiteKeys.all })}
      />
      <VersionHistory
        versions={site.versions}
        canManage={canManage}
        busy={busy}
        onRollBack={(version) => rollBack.mutate(version)}
      />
    </>
  )
}
