// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Query Key Factory
 *
 * Centralized query key management for React Query.
 * Provides type-safe, hierarchical keys for cache management.
 *
 * Pattern:
 * - Base keys: ["patients"] or ["sessions"]
 * - List keys: ["patients", "list", { search?, search_by? }]
 * - Detail keys: ["patients", "detail", patientId]
 *
 * This enables precise invalidation:
 * - Invalidate all patient queries: queryClient.invalidateQueries({ queryKey: queryKeys.patients.all })
 * - Invalidate patient lists only: queryClient.invalidateQueries({ queryKey: queryKeys.patients.lists() })
 * - Invalidate specific patient: queryClient.invalidateQueries({ queryKey: queryKeys.patients.detail(id) })
 *
 * @example
 * // Invalidate everything about patients
 * queryClient.invalidateQueries({ queryKey: queryKeys.patients.all })
 *
 * // Invalidate only patient lists (not individual patient details)
 * queryClient.invalidateQueries({ queryKey: queryKeys.patients.lists() })
 *
 * // Invalidate specific patient
 * queryClient.invalidateQueries({ queryKey: queryKeys.patients.detail("123") })
 *
 * // Invalidate a specific search
 * queryClient.invalidateQueries({
 *   queryKey: queryKeys.patients.list({ search: "Smith", search_by: "last_name" })
 * })
 *
 * Extending: a downstream build adds keys via `queryKeys.extensions.ts`
 * (the merge slot) — never by re-declaring this object. See that file.
 */

import { queryKeyExtensions } from "./queryKeys.extensions"
import type { PatientListParams } from "@/types/patients"

// --- Extension seam ---------------------------------------------------------
// A query-key value is a "leaf": a readonly tuple (a concrete key) or a
// key-factory function. Namespaces are plain objects we recurse into; leaves
// are replaced wholesale by an extension, objects deep-merge.
type QueryKeyLeaf = readonly unknown[] | ((...args: never[]) => unknown)

type Mergeable<T> = T extends QueryKeyLeaf
  ? false
  : T extends object
    ? true
    : false

type DeepMerge<A, B> = Mergeable<A> extends true
  ? Mergeable<B> extends true
    ? {
        [K in keyof A | keyof B]: K extends keyof A
          ? K extends keyof B
            ? DeepMerge<A[K], B[K]>
            : A[K]
          : K extends keyof B
            ? B[K]
            : never
      }
    : B
  : B

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    typeof value !== "function"
  )
}

/**
 * Deep-merge the extension slot into the base factory, per namespace. Plain
 * objects merge recursively; leaves (tuples / functions) from the extension
 * replace the base. With an empty extension this is the identity, so the OSS
 * build is byte-for-byte the base factory.
 */
function mergeQueryKeys<A, B>(base: A, ext: B): DeepMerge<A, B> {
  const out: Record<string, unknown> = { ...(base as Record<string, unknown>) }
  for (const [key, extValue] of Object.entries(ext as Record<string, unknown>)) {
    const baseValue = out[key]
    out[key] =
      isPlainObject(baseValue) && isPlainObject(extValue)
        ? mergeQueryKeys(baseValue, extValue)
        : extValue
  }
  return out as DeepMerge<A, B>
}

// --- Base factory -----------------------------------------------------------
// Internal references use `baseQueryKeys` (not the merged `queryKeys` export)
// so the base stays self-contained; the merged export is assembled below.
const baseQueryKeys = {
  // Dashboard summary (one aggregate read for the home screen)
  dashboard: {
    all: ["dashboard"] as const,
    summary: (params: { today: string; week: string }) =>
      [...baseQueryKeys.dashboard.all, "summary", params] as const,
  },

  // Patient query keys
  patients: {
    all: ["patients"] as const,
    lists: () => [...baseQueryKeys.patients.all, "list"] as const,
    list: (params?: PatientListParams) =>
      [...baseQueryKeys.patients.lists(), params] as const,
    details: () => [...baseQueryKeys.patients.all, "detail"] as const,
    detail: (patientId: string) =>
      [...baseQueryKeys.patients.details(), patientId] as const,
  },

  // Session query keys
  sessions: {
    all: ["sessions"] as const,
    lists: () => [...baseQueryKeys.sessions.all, "list"] as const,
    list: () => [...baseQueryKeys.sessions.lists()] as const,
    details: () => [...baseQueryKeys.sessions.all, "detail"] as const,
    detail: (sessionId: string) =>
      [...baseQueryKeys.sessions.details(), sessionId] as const,
    // Patient-specific sessions (for future use)
    byPatient: (patientId: string) =>
      [...baseQueryKeys.sessions.all, "byPatient", patientId] as const,
  },

  // Appointment query keys
  appointments: {
    all: ["appointments"] as const,
    lists: () => [...baseQueryKeys.appointments.all, "list"] as const,
    list: (params: { start: string; end: string }) =>
      [...baseQueryKeys.appointments.lists(), params] as const,
    details: () => [...baseQueryKeys.appointments.all, "detail"] as const,
    detail: (appointmentId: string) =>
      [...baseQueryKeys.appointments.details(), appointmentId] as const,
  },

  // Availability rule query keys
  availability: {
    all: ["availability"] as const,
    rules: () => [...baseQueryKeys.availability.all, "rules"] as const,
    slots: (date: string, duration?: number) =>
      [...baseQueryKeys.availability.all, "slots", date, duration ?? null] as const,
  },

  // User query keys
  user: {
    all: ["user"] as const,
    preferences: () => [...baseQueryKeys.user.all, "preferences"] as const,
    // Enrolled companion installs for the current user (desktop handoff).
    devices: () => [...baseQueryKeys.user.all, "devices"] as const,
  },

  // Note (clinical artifact) query keys
  notes: {
    all: ["notes"] as const,
    detail: (noteId: string) =>
      [...baseQueryKeys.notes.all, "detail", noteId] as const,
    byPatient: (patientId: string) =>
      [...baseQueryKeys.notes.all, "byPatient", patientId] as const,
  },

  // Outcome measure (scored instrument) query keys (PABLO-cwj)
  outcomeMeasures: {
    all: ["outcome-measures"] as const,
    detail: (measureId: string) =>
      [...baseQueryKeys.outcomeMeasures.all, "detail", measureId] as const,
    // Patient-level prefix — invalidate this to clear every instrument
    // variant for the patient (React Query matches keys by prefix).
    byPatientAll: (patientId: string) =>
      [...baseQueryKeys.outcomeMeasures.all, "byPatient", patientId] as const,
    byPatient: (patientId: string, instrument?: string) =>
      [
        ...baseQueryKeys.outcomeMeasures.byPatientAll(patientId),
        instrument ?? null,
      ] as const,
  },

  // Diagnostic-criteria engine query keys (PABLO-6xj)
  diagnoses: {
    all: ["diagnoses"] as const,
    // Global definition catalog (the rubric set), not patient-scoped.
    definitions: ["diagnoses", "definitions"] as const,
    detail: (assessmentId: string) =>
      [...baseQueryKeys.diagnoses.all, "detail", assessmentId] as const,
    // Patient-level prefix — invalidate this to clear every instrument
    // variant for the patient (React Query matches keys by prefix).
    byPatientAll: (patientId: string) =>
      [...baseQueryKeys.diagnoses.all, "byPatient", patientId] as const,
    byPatient: (patientId: string, instrument?: string) =>
      [
        ...baseQueryKeys.diagnoses.byPatientAll(patientId),
        instrument ?? null,
      ] as const,
  },

  // Patient document query keys (THERAPY-ak6m.2)
  patientDocuments: {
    all: ["patient-documents"] as const,
    byPatient: (patientId: string) =>
      [...baseQueryKeys.patientDocuments.all, "byPatient", patientId] as const,
    detail: (documentId: string) =>
      [...baseQueryKeys.patientDocuments.all, "detail", documentId] as const,
  },

  // Note-type catalog query keys
  noteTypes: {
    all: ["note-types"] as const,
    list: () => [...baseQueryKeys.noteTypes.all, "list"] as const,
    detail: (key: string) =>
      [...baseQueryKeys.noteTypes.all, "detail", key] as const,
  },

  // Compliance query keys (therapist-owned reminders)
  compliance: {
    all: ["compliance"] as const,
    templates: () => [...baseQueryKeys.compliance.all, "templates"] as const,
    items: () => [...baseQueryKeys.compliance.all, "items"] as const,
    documents: (itemId: string) =>
      [...baseQueryKeys.compliance.all, "documents", itemId] as const,
  },

  // Supervision / delegation query keys
  supervision: {
    all: ["supervision"] as const,
    list: () => [...baseQueryKeys.supervision.all, "list"] as const,
    hours: (id: string) =>
      [...baseQueryKeys.supervision.all, "hours", id] as const,
  },

  // Admin query keys
  admin: {
    all: ["admin"] as const,
    exportQueue: () => [...baseQueryKeys.admin.all, "export-queue"] as const,
    users: () => [...baseQueryKeys.admin.all, "users"] as const,
    allowlist: () => [...baseQueryKeys.admin.all, "allowlist"] as const,
    tenants: () => [...baseQueryKeys.admin.all, "tenants"] as const,
  },

  // Medication list query keys
  medications: {
    all: ["medications"] as const,
    byPatientAll: (patientId: string) =>
      [...baseQueryKeys.medications.all, "byPatient", patientId] as const,
    byPatient: (patientId: string, status?: string) =>
      [
        ...baseQueryKeys.medications.byPatientAll(patientId),
        status ?? null,
      ] as const,
  },

  // Availability rule query keys
  availabilityRules: {
    all: ["availabilityRules"] as const,
    list: () => [...baseQueryKeys.availabilityRules.all, "list"] as const,
  },

  // Self-pay card payment query keys
  payments: {
    all: ["payments"] as const,
    // Patient-level prefix — invalidate this to clear the card, the resolved
    // amount and the ledger in one go after a card change or a charge.
    byPatientAll: (patientId: string) =>
      [...baseQueryKeys.payments.all, "byPatient", patientId] as const,
    card: (patientId: string) =>
      [...baseQueryKeys.payments.byPatientAll(patientId), "card"] as const,
    charges: (patientId: string) =>
      [...baseQueryKeys.payments.byPatientAll(patientId), "charges"] as const,
    amount: (patientId: string, appointmentId?: string) =>
      [
        ...baseQueryKeys.payments.byPatientAll(patientId),
        "amount",
        appointmentId ?? null,
      ] as const,
  },

  // Booking link query keys
  bookingLinks: {
    all: ["bookingLinks"] as const,
    list: () => [...baseQueryKeys.bookingLinks.all, "list"] as const,
  },
} as const

export const queryKeys = mergeQueryKeys(baseQueryKeys, queryKeyExtensions)
