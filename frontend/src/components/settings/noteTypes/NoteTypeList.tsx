// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"use client"

import { FileText } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { useRetirePracticeNoteType } from "@/hooks/useNoteTypes"
import { isPracticeKey, PRACTICE_KEY_PREFIX, type NoteTypeSchema } from "@/types/noteTypes"
import { ListRow, SettingsCard } from "../ui"

interface NoteTypeListProps {
  noteTypes: NoteTypeSchema[]
  onEdit: (key: string) => void
}

/** The practice's own note types, newest version of each, with edit and retire. */
export function NoteTypeList({ noteTypes, onEdit }: NoteTypeListProps) {
  const retire = useRetirePracticeNoteType()
  const [confirming, setConfirming] = useState<string | null>(null)
  const own = noteTypes.filter((t) => isPracticeKey(t.key) && t.version != null)

  return (
    <SettingsCard title="Your note types">
      {own.length === 0 ? (
        <p className="text-sm text-muted-foreground">You haven&apos;t made a note type yet.</p>
      ) : (
        <ul>
          {own.map((t) => (
            <ListRow
              key={t.key}
              icon={FileText}
              title={t.label}
              subtitle={
                confirming === t.key
                  ? "Retire it? New appointments can't use it. Notes already written with it are unchanged."
                  : `Version ${t.version}${t.description ? ` · ${t.description}` : ""}`
              }
            >
              {confirming === t.key ? (
                <>
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={retire.isPending}
                    onClick={() =>
                      retire.mutate(t.key.slice(PRACTICE_KEY_PREFIX.length), { onSuccess: () => setConfirming(null) })
                    }
                  >
                    Retire
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirming(null)}>
                    Keep
                  </Button>
                </>
              ) : (
                <>
                  <Button size="sm" variant="outline" aria-label={`Edit ${t.label}`} onClick={() => onEdit(t.key)}>
                    Edit
                  </Button>
                  <Button size="sm" variant="ghost" aria-label={`Retire ${t.label}`} onClick={() => setConfirming(t.key)}>
                    Retire
                  </Button>
                </>
              )}
            </ListRow>
          ))}
        </ul>
      )}
      {retire.error && (
        <p role="alert" className="mt-2 text-[12.5px] text-red-700">
          That note type couldn&apos;t be retired. Try again.
        </p>
      )}
    </SettingsCard>
  )
}
