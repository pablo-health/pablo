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
 */

import type { PatientListParams } from "@/types/patients"

export const queryKeys = {
  // Patient query keys
  patients: {
    all: ["patients"] as const,
    lists: () => [...queryKeys.patients.all, "list"] as const,
    list: (params?: PatientListParams) =>
      [...queryKeys.patients.lists(), params] as const,
    details: () => [...queryKeys.patients.all, "detail"] as const,
    detail: (patientId: string) =>
      [...queryKeys.patients.details(), patientId] as const,
  },

  // Session query keys
  sessions: {
    all: ["sessions"] as const,
    lists: () => [...queryKeys.sessions.all, "list"] as const,
    list: () => [...queryKeys.sessions.lists()] as const,
    details: () => [...queryKeys.sessions.all, "detail"] as const,
    detail: (sessionId: string) =>
      [...queryKeys.sessions.details(), sessionId] as const,
    // Patient-specific sessions (for future use)
    byPatient: (patientId: string) =>
      [...queryKeys.sessions.all, "byPatient", patientId] as const,
  },

  // Appointment query keys
  appointments: {
    all: ["appointments"] as const,
    lists: () => [...queryKeys.appointments.all, "list"] as const,
    list: (params: { start: string; end: string }) =>
      [...queryKeys.appointments.lists(), params] as const,
    details: () => [...queryKeys.appointments.all, "detail"] as const,
    detail: (appointmentId: string) =>
      [...queryKeys.appointments.details(), appointmentId] as const,
  },

  // User query keys
  user: {
    all: ["user"] as const,
    preferences: () => [...queryKeys.user.all, "preferences"] as const,
  },

  // Note (clinical artifact) query keys
  notes: {
    all: ["notes"] as const,
    detail: (noteId: string) =>
      [...queryKeys.notes.all, "detail", noteId] as const,
    byPatient: (patientId: string) =>
      [...queryKeys.notes.all, "byPatient", patientId] as const,
  },

  // Note-type catalog query keys
  noteTypes: {
    all: ["note-types"] as const,
    list: () => [...queryKeys.noteTypes.all, "list"] as const,
    detail: (key: string) =>
      [...queryKeys.noteTypes.all, "detail", key] as const,
  },

  // Compliance query keys (therapist-owned reminders)
  compliance: {
    all: ["compliance"] as const,
    templates: () => [...queryKeys.compliance.all, "templates"] as const,
    items: () => [...queryKeys.compliance.all, "items"] as const,
  },

  // Admin query keys
  admin: {
    all: ["admin"] as const,
    exportQueue: () => [...queryKeys.admin.all, "export-queue"] as const,
    users: () => [...queryKeys.admin.all, "users"] as const,
    allowlist: () => [...queryKeys.admin.all, "allowlist"] as const,
    tenants: () => [...queryKeys.admin.all, "tenants"] as const,
  },
} as const
