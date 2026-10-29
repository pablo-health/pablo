// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

import { structuredToNarrative } from "@/components/sessions/SubFieldEditor"
import type { Note } from "@/types/notes"
import type { NoteContent, StructuredSOAPNoteModel } from "@/types/sessions"
import {
  noteContentFromNote,
  noteEditedContentFromNote,
  structuredSoapFromNote,
} from "@/types/sessions"

/**
 * The body a note shows: the clinician's edit when there is one, otherwise
 * the original. The note view and the PDF export both read it from
 * {@link displayedNote}, so what is on screen and what is exported cannot
 * drift apart.
 */
export interface DisplayedNote {
  /** What to show, or null when the note has no content yet. */
  content: NoteContent | null
  /** True when `content` is the clinician's edit rather than the original. */
  edited: boolean
  /**
   * The structured SOAP draft, with its source references, while the
   * original is what shows. Null once the note is edited: the references
   * point at the draft's sentences, not the clinician's.
   */
  draft: StructuredSOAPNoteModel | null
}

export function displayedNote(
  note: Pick<Note, "note_type" | "content" | "content_edited">,
  pendingEdited?: NoteContent | null,
): DisplayedNote {
  const edited = pendingEdited ?? noteEditedContentFromNote(note)
  if (edited) return { content: edited, edited: true, draft: null }

  // A drafted SOAP note is stored structured only, with no narrative beside
  // it; the editor and the PDF both work from narrative, so derive it here.
  const draft = structuredSoapFromNote(note)
  if (draft && !draft.narrative) {
    return {
      content: { note_type: "soap", ...structuredToNarrative(draft) },
      edited: false,
      draft,
    }
  }
  return { content: noteContentFromNote(note), edited: false, draft }
}
