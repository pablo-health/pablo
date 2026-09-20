// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import { SettingsBadge, SettingsCard } from "@/components/settings/ui"
import { Button } from "@/components/ui/button"
import {
  useCreateIntakeTemplate,
  useCreateIntakeVersion,
  useIntakeTemplates,
  useIntakeVersion,
  usePublishIntakeVersion,
  useSaveIntakeItems,
} from "@/hooks/useIntakePackets"
import type { IntakeItemInput, IntakeTemplate } from "@/types/intakePackets"
import { IntakeItemEditor } from "./IntakeItemEditor"
import {
  DRAFT_BADGE,
  EMPTY_STATE,
  FORMS_DESCRIPTION,
  FORMS_TITLE,
  NEW_FORM_NAME,
  NEW_VERSION_BUTTON,
  PUBLISHED_BADGE,
} from "./intakeCopy"

/** What the server said, or a plain fallback if it said nothing readable. */
function messageOf(error: unknown): string | null {
  if (!error) return null
  if (error instanceof Error && error.message) return error.message
  return "That could not be saved."
}

function latestVersion(template: IntakeTemplate) {
  return template.versions[0]
}

/**
 * Practice > Patient portal > Forms.
 *
 * A list of the practice's intake forms, and the questions on whichever
 * version is open. One form is selected at a time: a practice edits the form
 * it is thinking about, and a page of every version of every form would be a
 * page nobody reads.
 */
export function IntakeFormsCard() {
  const { data: templates } = useIntakeTemplates()
  const createTemplate = useCreateIntakeTemplate()
  const createVersion = useCreateIntakeVersion()
  const saveItems = useSaveIntakeItems()
  const publish = usePublishIntakeVersion()

  const [openTemplateId, setOpenTemplateId] = useState<string | null>(null)
  const [openVersionId, setOpenVersionId] = useState<string | null>(null)

  const list = templates ?? []
  const openTemplate = list.find((t) => t.id === openTemplateId) ?? null
  const versionId =
    openVersionId ?? (openTemplate ? (latestVersion(openTemplate)?.id ?? null) : null)
  const { data: version } = useIntakeVersion(openTemplate?.id, versionId ?? undefined)

  function openForm(template: IntakeTemplate) {
    const same = openTemplateId === template.id
    setOpenTemplateId(same ? null : template.id)
    setOpenVersionId(null)
  }

  function handleSave(items: IntakeItemInput[]) {
    if (!openTemplate || !versionId) return
    saveItems.mutate({ templateId: openTemplate.id, versionId, items })
  }

  function handlePublish() {
    if (!openTemplate || !versionId) return
    publish.mutate({ templateId: openTemplate.id, versionId })
  }

  function handleNewVersion() {
    if (!openTemplate) return
    createVersion.mutate(openTemplate.id, {
      onSuccess: (draft) => setOpenVersionId(draft.id),
    })
  }

  return (
    <SettingsCard title={FORMS_TITLE} description={FORMS_DESCRIPTION}>
      {list.length === 0 && <p className="text-[13px] text-muted-foreground">{EMPTY_STATE}</p>}

      <ul className="space-y-2">
        {list.map((template) => {
          const current = latestVersion(template)
          const open = openTemplateId === template.id
          return (
            <li key={template.id} className="rounded-xl border border-border p-3">
              <div className="flex items-center justify-between gap-3">
                <button
                  type="button"
                  className="min-w-0 flex-1 text-left"
                  onClick={() => openForm(template)}
                  aria-expanded={open}
                >
                  <span className="text-sm font-semibold text-foreground">{template.name}</span>
                </button>
                {current && (
                  <SettingsBadge>
                    {current.published_at ? PUBLISHED_BADGE : DRAFT_BADGE}
                  </SettingsBadge>
                )}
              </div>

              {open && version && (
                <div className="mt-3 space-y-3 border-t border-border pt-3">
                  <div className="flex items-center gap-2">
                    {template.versions.map((v) => (
                      <Button
                        key={v.id}
                        type="button"
                        size="sm"
                        variant={v.id === versionId ? "default" : "ghost"}
                        onClick={() => setOpenVersionId(v.id)}
                      >
                        {`Version ${v.version}`}
                      </Button>
                    ))}
                    {version.published_at && (
                      <Button
                        type="button"
                        size="sm"
                        variant="outline"
                        onClick={handleNewVersion}
                        disabled={createVersion.isPending}
                      >
                        {NEW_VERSION_BUTTON}
                      </Button>
                    )}
                  </div>
                  <IntakeItemEditor
                    version={version}
                    onSave={handleSave}
                    onPublish={handlePublish}
                    saving={saveItems.isPending}
                    publishing={publish.isPending}
                    publishError={messageOf(publish.error) ?? messageOf(saveItems.error)}
                  />
                </div>
              )}
            </li>
          )
        })}
      </ul>

      <div className="mt-3">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => createTemplate.mutate(NEW_FORM_NAME)}
          disabled={createTemplate.isPending}
        >
          <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
          Add a form
        </Button>
      </div>
    </SettingsCard>
  )
}
