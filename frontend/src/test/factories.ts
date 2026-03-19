// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Test Factories
 *
 * Factory functions for creating type-safe mock objects in tests.
 * Use these instead of inline mocks to ensure mocks stay in sync with types.
 */

import type {
  SessionResponse,
  SOAPNoteModel,
  SOAPSentence,
  StructuredSOAPNoteModel,
} from "@/types/sessions"
import type { PatientResponse } from "@/types/patients"

function s(
  text: string,
  ids: number[] = [],
  confidence: { score?: number; level?: string } = {},
): SOAPSentence {
  return {
    text,
    source_segment_ids: ids,
    confidence_score: confidence.score ?? 0.0,
    confidence_level: confidence.level ?? "",
    possible_match_segment_ids: [],
    signal_used: "",
  }
}

function sl(items: string[]): SOAPSentence[] {
  return items.map((t) => s(t))
}

/**
 * Creates a mock SessionResponse with all required fields.
 * Override specific fields as needed for your test case.
 */
export function createMockSession(
  overrides: Partial<SessionResponse> = {}
): SessionResponse {
  return {
    id: "session-123",
    user_id: "user-1",
    patient_id: "patient-456",
    patient_name: "Doe, Jane",
    session_date: "2024-01-15T14:30:00Z",
    session_number: 1,
    status: "pending_review",
    transcript: { format: "vtt", content: "WEBVTT\n\n..." },
    created_at: "2024-01-15T14:30:00Z",
    soap_note: null,
    soap_note_edited: null,
    soap_note_structured: null,
    transcript_segments: null,
    quality_rating: null,
    quality_rating_reason: null,
    quality_rating_sections: null,
    processing_started_at: null,
    processing_completed_at: null,
    finalized_at: null,
    error: null,
    redacted_transcript: null,
    naturalized_transcript: null,
    redacted_soap_note: null,
    naturalized_soap_note: null,
    export_status: "not_queued",
    export_queued_at: null,
    export_reviewed_at: null,
    export_reviewed_by: null,
    exported_at: null,
    ...overrides,
  }
}

/**
 * Creates a mock PatientResponse with all required fields.
 * Override specific fields as needed for your test case.
 */
export function createMockPatient(
  overrides: Partial<PatientResponse> = {}
): PatientResponse {
  return {
    id: "patient-123",
    user_id: "user-1",
    first_name: "Jane",
    last_name: "Doe",
    email: null,
    phone: null,
    status: "active",
    date_of_birth: null,
    diagnosis: null,
    session_count: 0,
    last_session_date: null,
    next_session_date: null,
    created_at: "2024-01-15T10:00:00Z",
    updated_at: "2024-01-15T10:00:00Z",
    ...overrides,
  }
}

/**
 * Creates a mock SOAP note (narrative strings) with all required fields.
 */
export function createMockSOAPNote(
  overrides: Partial<SOAPNoteModel> = {}
): SOAPNoteModel {
  return {
    subjective: "Patient reports feeling anxious.",
    objective: "Patient appeared calm during session.",
    assessment: "Continued progress in managing anxiety.",
    plan: "Continue weekly sessions.",
    ...overrides,
  }
}

/**
 * Creates a mock structured SOAP note with sub-fields and derived narrative.
 */
export function createMockStructuredSOAPNote(
  overrides: Partial<StructuredSOAPNoteModel> = {}
): StructuredSOAPNoteModel {
  return {
    subjective: {
      chief_complaint: s("Patient reports feeling anxious.", [0, 1]),
      mood_affect: s("Anxious but cooperative.", [2]),
      symptoms: sl(["Difficulty sleeping", "Racing thoughts"]),
      client_narrative: s("Describes increased stress at work.", [1, 3]),
    },
    objective: {
      appearance: s("Well-groomed, appropriately dressed."),
      behavior: s("Cooperative, good eye contact."),
      speech: s("Normal rate and volume."),
      thought_process: s("Linear and goal-directed."),
      affect_observed: s("Congruent with reported mood."),
    },
    assessment: {
      clinical_impression: s("Continued progress in managing anxiety.", [0, 4]),
      progress: s("Moderate improvement since last session.", [5]),
      risk_assessment: s("No acute safety concerns.", [6]),
      functioning_level: s("Moderate."),
    },
    plan: {
      interventions_used: sl(["CBT cognitive restructuring"]),
      homework_assignments: sl(["Practice mindfulness daily"]),
      next_steps: sl(["Review progress next session"]),
      next_session: s("One week."),
    },
    narrative: createMockSOAPNote(),
    ...overrides,
  }
}
