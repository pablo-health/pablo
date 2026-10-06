// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Note types a practice can start from instead of a blank page.
 *
 * Each template is a JSON file beside this one: the `spec` the save route
 * takes, in the exact shape the server stores (backend
 * `tests/test_note_type_templates.py` holds it to that), plus synthetic sample
 * visits for Try it. They are data so the transcripts — which name a client in
 * every line — stay out of interface copy.
 */

import type { PracticeNoteTypeSpec } from "@/types/noteTypes"
import psychiatricFollowUp from "./templates/psychiatric_follow_up.json"

/** A synthetic visit transcript to try a template's draft on. */
export interface SampleVisit {
  id: string
  label: string
  transcript: string
}

export interface NoteTypeTemplate {
  id: string
  /** The slug a new type from this template saves under, when it is free. */
  slug: string
  spec: PracticeNoteTypeSpec
  samples: SampleVisit[]
}

export const NOTE_TYPE_TEMPLATES: NoteTypeTemplate[] = [psychiatricFollowUp as NoteTypeTemplate]
